# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**health-ai-service** — FastAPI backend for medical consultation: RAG, conversation memory, intent classification, content safety. Called service-to-service from Laravel (token auth) and from clients via JWT. Python 3.11 + OpenAI + Qdrant + Redis. Locales: `ru`/`en`/`kk`.

HTTP contract for external consumers lives in [API_CONTRACT.md](API_CONTRACT.md) — keep it in sync with routes/schemas.

## Commands

```bash
pip install -r requirements.txt               # runtime
pip install -r requirements-dev.txt           # + lint/type/security (matches CI)
cp .env.example .env                          # set OPENAI_API_KEY, SERVICE_TOKEN (CSV for rotation)

uvicorn app.main:app --reload --port 8001     # local
docker compose up -d                          # dev stack (Redis + Qdrant + service; CMD is gunicorn)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d

# CI gates
ruff check app/ scripts/
ruff format --check app/ scripts/
mypy app/                                     # advisory in CI, keep clean locally
bandit -r app/ -lll
pip-audit -r requirements.txt

# Tests (pytest.ini enforces --cov-fail-under=80, scoped to app/)
pytest
pytest tests/test_chat_router.py::test_chat_happy_path -v
pytest --no-cov tests/test_llm.py             # skip coverage while iterating
```

Fixtures in [tests/conftest.py](tests/conftest.py) mock Redis/Qdrant/OpenAI (`mock_redis`, `mock_qdrant`, `mock_openai_client`, `auth_client`). Security tests → [tests/security/](tests/security/); multi-step flows → [tests/integration/](tests/integration/).

## Architecture

### `/v1/chat` + `/v1/chat/stream` pipeline ([app/routers/chat.py](app/routers/chat.py))

Same pipeline, JSON vs SSE (`meta`/`delta`/`final`/`error`):
1. Auth ([security.py](app/security.py)) — JWT RS256 or `X-Service-Token` + `X-User-Id`.
2. Rate limit ([rate_limit.py](app/services/rate_limit.py)) — per-user per-minute via Redis; cap = `RATE_LIMIT_PER_MINUTE + RATE_LIMIT_BURST`.
3. Prompt-injection guard ([safety.py](app/services/safety.py)) — regex; refusal skips LLM.
4. Owner check — `conversation_id` owner in Redis must match user_id (403 else).
5. History — from body (last 8 turns) or Redis.
6. Intent ([intent.py](app/services/intent.py)) — cheap LLM call, cached 300s. `off_topic` with confidence ≥0.7 short-circuits.
7. Summarization ([summarizer.py](app/services/summarizer.py)) — >8 turns → summary + last 6 (summary *replaces* older history, doesn't prepend).
8. RAG ([rag.py](app/services/rag.py), [vector_store.py](app/services/vector_store.py)) — cached embedding, Qdrant w/ language filter, drop below `RAG_SCORE_THRESHOLD`, cross-language fallback. **Fails open**.
9. Circuit breaker ([circuit_breaker.py](app/services/circuit_breaker.py)) — **fails closed**: opens after 3 OpenAI failures/60s → 503.
10. LLM ([llm.py](app/services/llm.py)) — system + locale addon (via `intent.addon_name`) + summary + RAG + history. Temperature from `CATEGORY_TO_TEMPERATURE`.
11. Content filter ([content_filter.py](app/services/content_filter.py)) — softens diagnoses, appends doctor note near drug dosages.
12. Persist — Redis `RPUSH`/`LTRIM` to `REDIS_MAX_TURNS`, owner via `SET NX` (first writer wins).

### Singletons & lifespan

[app/main.py](app/main.py) lifespan initializes Redis/Qdrant/OpenAI singletons and calls `ensure_qdrant_collection` (**refuses to start on vector-size mismatch** — recreate collection or set new `QDRANT_COLLECTION`). Use `get_redis()`/`get_qdrant()`; never instantiate `AsyncOpenAI` directly. Graceful shutdown waits 30s for streams registered via `register_stream` — must stay aligned with Dockerfile's `--graceful-timeout 30`.

### Redis keys (prefix `REDIS_PREFIX`, default `healthai`)

`conv:{id}:turns|owner|summary|meta`, `rl:{user_id}:{minute}`, `emb:{md5}`, `intent:{md5}`.

### Config knobs ([app/config.py](app/config.py), pydantic-settings)

- `RAG_SCORE_THRESHOLD` (0.35) — too high = "RAG silently went cold".
- `OPENAI_MAX_RETRIES` (3), `OPENAI_TIMEOUT_SECONDS` (30).
- `APP_ENV=production` — disables `/docs`+`/redoc`, rejects `ALLOWED_ORIGINS=*` w/ credentials.
- `ENABLE_DEV_ROUTES` — mounts `/v1/rag/*` (keep false in prod).
- `LOG_FORMAT=json|text`.

## Development Patterns

- **Prompts** ([app/prompts.py](app/prompts.py)) — `SYSTEM_PROMPTS`, `DISCLAIMERS`, `ADDON_PROMPTS` are all keyed by `ru`/`en`/`kk`. Always update all three; unknown locales fold to `ru` (no error).
- **New intent category**: add to `VALID_CATEGORIES`, `CATEGORY_TO_TEMPERATURE`, `CATEGORY_TO_ADDON` in [intent.py](app/services/intent.py); extend `CLASSIFY_SYSTEM_PROMPT`; add locale addon in [prompts.py](app/prompts.py) if needed.
- **RAG chunks**: every chunk's `payload.language` must be set — search filter relies on it.
- **Endpoint contracts**: when changing a route, update decorator `summary`/`description`/`responses`, Pydantic `json_schema_extra` example, and [API_CONTRACT.md](API_CONTRACT.md) — all three must stay in sync.
- **Service token rotation**: `SERVICE_TOKEN` is comma-separated; any listed token is valid.
- **PII / logging**: medical content (user messages, profile details, conversation turns, LLM answers) MUST NOT appear in application logs. Only log identifiers (`request_id`, `conversation_id`, `user_id`, `intent.category`) and metadata (`duration_ms`, token counts). A `PIIRedactorFilter` in [logging_config.py](app/logging_config.py) catches accidental leaks via `extra={"user_message": ...}` and friends — extend `_REDACT_KEYS` if new PII fields appear. For debugging that needs payload context, prefer tracing spans (which don't persist by default) over logs.

### Knowledge base

Curated Markdown under [data/knowledge_base/](data/knowledge_base/) + [manifest.json](data/knowledge_base/manifest.json). Seed via `python scripts/seed_knowledge_base.py --manifest data/knowledge_base/manifest.json` (idempotent); verify via `python scripts/verify_knowledge_base.py`. Both hit dev-only endpoints (`ENABLE_DEV_ROUTES=true`) via `X-Service-Token`. When adding articles, also update [data/knowledge_base/LICENSE.md](data/knowledge_base/LICENSE.md).

## Deployment

- Multi-stage Dockerfile; runtime CMD is `gunicorn -k uvicorn.workers.UvicornWorker -w 4 --graceful-timeout 30`.
- Prod = **overlay**: `docker-compose.yml` + `docker-compose.prod.yml` (not the prod file alone). `.env.production` is deploy-host only (gitignored).
- `/health` → `ok|degraded` (Redis + Qdrant + circuit state); wire to liveness probes.
- `/metrics` is unauthenticated — keep on private network.
- Release: push `vX.Y.Z` tag → [.github/workflows/deploy.yml](.github/workflows/deploy.yml) builds + pushes to GHCR. CI = [.github/workflows/ci.yml](.github/workflows/ci.yml) (test/lint/typecheck/security).

## Tools

Use the Context7 MCP tool for FastAPI, OpenAI SDK, Qdrant, Redis, pydantic docs before guessing at API shapes.
