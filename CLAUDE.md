# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**health-ai-service** is a FastAPI backend that provides medical consultation via an AI assistant with RAG (Retrieval-Augmented Generation), conversation memory, intent classification, and content safety. It is called from a Laravel backend as a service-to-service dependency (service token auth) and from user clients via JWT. Python 3.11 + OpenAI API + Qdrant + Redis.

**Languages supported**: Russian, English, Kazakh (ru/en/kk).

The product roadmap lives in [PRODUCTION_COMPLETION_PLAN.md](PRODUCTION_COMPLETION_PLAN.md). Phases 1–12 are complete: cognitive prompts → intent routing → RAG → memory → security → reliability → observability → API docs & Laravel contract → Docker & deployment hardening → CI/CD → knowledge-base seeding (curated ru/en/kk corpus + CLI tooling). CI lives under [.github/workflows/](.github/workflows/) (`ci.yml` for tests/lint/typecheck/security scans, `deploy.yml` for tagged GHCR image builds).

The authoritative HTTP contract for external consumers (Laravel, Swift) lives in [API_CONTRACT.md](API_CONTRACT.md). Keep it in sync when changing routes, request/response shapes, or headers.

## Quick Commands

### Setup & Development
```bash
# Runtime only
pip install -r requirements.txt

# Runtime + lint/type/security toolchain (matches CI)
pip install -r requirements-dev.txt

cp .env.example .env
# Edit .env: OPENAI_API_KEY, SERVICE_TOKEN (comma-separated for rotation), etc.

uvicorn app.main:app --reload --port 8001

# Or the full dev stack (Redis + Qdrant + AI service) — the image CMD is gunicorn, not uvicorn.
docker compose up -d

# Production: layered compose + .env.production (see Deployment Notes below)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d
```

### Lint, type, and security (same gates as CI)
```bash
ruff check app/ scripts/
ruff format --check app/ scripts/     # use `ruff format app/ scripts/` to auto-apply
mypy app/                              # advisory in CI (continue-on-error), but keep clean locally
bandit -r app/ -lll                    # high-severity only
pip-audit -r requirements.txt
```
Ruff + mypy are configured in [pyproject.toml](pyproject.toml); tests under `tests/integration/` and `tests/security/` are excluded from ruff. The seed/verify CLIs under [scripts/](scripts/) are in scope — keep them clean too. Dev tool versions are pinned in [requirements-dev.txt](requirements-dev.txt) — bump them there, not ad-hoc.

### Testing
```bash
# Full suite — pytest.ini enforces --cov-fail-under=80
pytest

# Single file / single test
pytest tests/test_chat_router.py -v
pytest tests/test_chat_router.py::test_chat_happy_path -v

# Skip coverage gate while iterating
pytest --no-cov tests/test_llm.py

# Only integration or security suites
pytest tests/integration/
pytest tests/security/
```

Unit tests mock Redis, Qdrant, and OpenAI through fixtures in [tests/conftest.py](tests/conftest.py) — most tests run without live services. Integration tests under [tests/integration/](tests/integration/) also mock externals but exercise full request flows through the ASGI app. The 80% coverage gate in [pytest.ini](pytest.ini) is scoped to `app/` only (`--cov=app`), so CLI code under [scripts/](scripts/) doesn't drag the number down, but the suite still has dedicated unit coverage for the seed/verify scripts.

## Architecture

### Request flow for `/v1/chat` and `/v1/chat/stream`

Both endpoints run the same pipeline in [app/routers/chat.py](app/routers/chat.py); only the transport differs (JSON vs SSE with `meta`/`delta`/`final`/`error` events):

1. **Auth** ([security.py](app/security.py)) — JWT (RS256) via `Authorization: Bearer`, or `X-Service-Token` + `X-User-Id`.
2. **Context vars** — request_id (set by middleware), conversation_id, user_id flow through logs via [context.py](app/context.py).
3. **Rate limit** ([rate_limit.py](app/services/rate_limit.py)) — Redis `INCR`+`EXPIRE` on a per-minute window keyed by user. Cap = `RATE_LIMIT_PER_MINUTE + RATE_LIMIT_BURST`.
4. **Prompt-injection guard** ([safety.py](app/services/safety.py)) — regex-based detection; refusal returned verbatim without calling the LLM. Input is also sanitized (strip `<script>`, `javascript:`, null bytes).
5. **Owner check** — if the caller passed a `conversation_id`, its owner in Redis must match the resolved user_id (403 otherwise).
6. **History load** — from the request body (trimmed to last 8 turns) or Redis when empty.
7. **Intent classification** ([intent.py](app/services/intent.py)) — cheap LLM call returns JSON `{category, confidence, risk_level, requires_followup, detected_entities}`. Cached in Redis for 300 s. Off-topic messages with confidence ≥ 0.7 short-circuit with a canned response and skip the main LLM call.
8. **Summarization** ([summarizer.py](app/services/summarizer.py)) — when history > 8 turns, the older portion is summarized (cached) and only the most recent 6 turns are sent to the LLM alongside the summary.
9. **RAG** ([rag.py](app/services/rag.py) + [vector_store.py](app/services/vector_store.py)) — embed query (cached in Redis), Qdrant search with language filter, drop chunks below `RAG_SCORE_THRESHOLD`, cross-language fallback if too few survive. RAG failures are swallowed — LLM runs without context rather than 500ing.
10. **Circuit breaker** ([circuit_breaker.py](app/services/circuit_breaker.py)) — if `openai_breaker.is_available` is false, returns 503 (or SSE `error`) with a localized "degraded" message without calling the API.
11. **LLM** ([llm.py](app/services/llm.py)) — system prompt + locale addon (chosen by `intent.addon_name`: `symptom_check` / `lifestyle` / `mental_health` / `emergency`) + summary + RAG block + history + user message. Temperature is driven by intent category (`CATEGORY_TO_TEMPERATURE`).
12. **Content safety** ([content_filter.py](app/services/content_filter.py)) — post-processes LLM output: softens definitive diagnoses, appends "consult your doctor" note next to drug dosages.
13. **Persist** — append user+assistant turns to Redis (`RPUSH`/`LTRIM` to `REDIS_MAX_TURNS`, TTL refreshed), owner stored with `SET NX`, metadata (topic, turn_count) updated.
14. **Respond** — `ChatResponse` with answer, disclaimer, conversation_id, rag_used/sources, and intent.

### Module map

- **[app/main.py](app/main.py)** — app factory, CORS, lifespan (init/close Redis+Qdrant, `ensure_qdrant_collection`), graceful shutdown of in-flight streams via `register_stream`/`_shutdown_event` (30 s timeout), `/health` (liveness + circuit state), `/metrics` (snapshot + live Redis/Qdrant sizes). `/docs` + `/redoc` are disabled when `APP_ENV=production`; OpenAPI tags and description come from the same constructor.
- **[app/middleware/request_logging.py](app/middleware/request_logging.py)** — generates/propagates `X-Request-Id`, records per-request latency and status into `metrics`.
- **[app/middleware/api_version.py](app/middleware/api_version.py)** — raw ASGI middleware stamping `X-API-Version: v1` and `X-Service-Version: <app_version>` on every response. Register order matters: sits **after** `RequestLoggingMiddleware` so the latter's request ID is present on errors too.
- **[app/logging_config.py](app/logging_config.py)** — text or JSON formatter (`LOG_FORMAT=text|json`), injects request/conversation/user IDs via `ContextFilter`.
- **[app/metrics.py](app/metrics.py)** — in-process thread-safe counters (requests, intents, OpenAI tokens, RAG hit rate, error rate 1h). Exposed at `GET /metrics`.
- **[app/prompts.py](app/prompts.py)** — `SYSTEM_PROMPTS` (cognitive reasoning + safety rules per locale), `DISCLAIMERS`, `ADDON_PROMPTS` keyed by intent category.
- **[app/routers/](app/routers/)** — `chat.py`, `conversations.py` (owner-enforced GET/DELETE by id), `articles.py` (upload → chunk → index), `rag.py` (dev only, gated by `ENABLE_DEV_ROUTES`).
- **[app/services/](app/services/)** — one file per responsibility. `redis_client.py` / `vector_client.py` are singletons initialized in the lifespan; call `get_redis()`/`get_qdrant()` anywhere afterward. `openai_client.py` is a module-level singleton too — don't instantiate `AsyncOpenAI` directly.
- **[app/schemas.py](app/schemas.py)** — `ChatRequest` validates `conversation_id` as UUID, caps message at 4000 chars, metadata at 5 KB, per-list limits (20 items × 200 chars) on profile fields. All top-level request/response models carry `json_schema_extra={"examples": [...]}` for Swagger rendering — update the example when you change a shape, or Laravel/Swift integrators will copy stale payloads out of `/docs`.

### Design decisions that aren't obvious from code

- **Fail-open for RAG**, **fail-closed for OpenAI**. A Qdrant outage logs a warning and lets the LLM answer without context. The circuit breaker opens after 3 OpenAI failures within 60 s and returns a localized 503 until a probe succeeds.
- **Embedding and intent caches live in Redis** (`EMBEDDING_CACHE_TTL` = 86400 s, intent = 300 s). Cache keys are namespaced with `REDIS_PREFIX` — invalidate by flushing those prefixes, not the whole DB.
- **Summary replaces history, doesn't prepend to it.** Once summarization kicks in at >8 turns, the LLM gets `summary + last 6 turns`, not the full log. The summary is cached until the conversation is deleted or expires.
- **Owner is stored with `SET NX`** so the first writer wins — you can't overwrite another user's conversation_id.
- **Service token supports rotation**: `SERVICE_TOKEN` is comma-separated; any token in the set is valid. Deploy a new token, switch callers over, then drop the old one.
- **`ensure_qdrant_collection` refuses to start** when the stored vector size doesn't match the embedding model. Recreate the collection (dev) or set a new `QDRANT_COLLECTION` name (prod) when changing models.

### Redis key layout

All prefixed with `REDIS_PREFIX` (default `healthai`):
- `...:conv:{id}:turns` — list of JSON-encoded `Turn`s (old→new), capped to `REDIS_MAX_TURNS`
- `...:conv:{id}:owner` — user id that created the conversation
- `...:conv:{id}:summary` — cached summarization
- `...:conv:{id}:meta` — JSON metadata (topic, turn_count, …)
- `...:rl:{user_id}:{minute}` — rate-limit counter
- `...:emb:{md5}` — cached embedding vector
- `...:intent:{md5}` — cached intent classification

### Configuration

All settings load through [app/config.py](app/config.py) (pydantic-settings, reads `.env`). Non-obvious knobs:

- `OPENAI_MAX_RETRIES` (3), `OPENAI_TIMEOUT_SECONDS` (30) — honored by the SDK client in [openai_client.py](app/services/openai_client.py).
- `RAG_SCORE_THRESHOLD` (0.35) — chunks below this cosine score are dropped before reaching the LLM. A too-high threshold is the most common cause of "RAG silently went cold."
- `RATE_LIMIT_PER_MINUTE` + `RATE_LIMIT_BURST` — effective per-user cap is the sum (25 default).
- `LOG_FORMAT=json` for structured logs in production; `text` for local.
- `ALLOWED_ORIGINS` = `*` logs a warning when `APP_ENV=production` and forces `allow_credentials=false`. Set it to a CSV of real origins to enable credentials.
- `ENABLE_DEV_ROUTES` — mounts `/v1/rag` management endpoints; keep false in prod.

## Development Patterns

### Changing user-visible prompts or disclaimers

Every prompt in [app/prompts.py](app/prompts.py) is keyed by `ru` / `en` / `kk`. **Always update all three.** `i18n.normalize_locale` folds unknown locales to `ru`, so forgetting Kazakh silently breaks Kazakh users rather than erroring.

### Adding an intent category

1. Add the label to `VALID_CATEGORIES`, pick a temperature in `CATEGORY_TO_TEMPERATURE`, wire it to an addon name in `CATEGORY_TO_ADDON` (or `None` for the base prompt) in [intent.py](app/services/intent.py).
2. Extend `CLASSIFY_SYSTEM_PROMPT` with the rule for when to pick it.
3. If it needs a dedicated addon, add three locale entries to `ADDON_PROMPTS` in [prompts.py](app/prompts.py).
4. Optional: add a short-circuit branch in `chat.py` (like `off_topic` does) if the category should skip the LLM entirely.

### Touching the RAG pipeline

- New chunks go through [article_parser.py](app/services/article_parser.py) → `upsert_text_chunks` in [vector_store.py](app/services/vector_store.py). Every chunk's `payload.language` must be set — the search filter relies on it.
- Changing the embedding model means a new vector dimension; see the guardrail in `ensure_qdrant_collection`.

### Writing tests

- Prefer the existing fixtures (`mock_redis`, `mock_qdrant`, `mock_openai_client`, `auth_client`) over building a new ASGI transport. `auth_client` already injects the test service token.
- Coverage gate is 80% in [pytest.ini](pytest.ini); new modules need tests or the full run fails. Use `--no-cov` while iterating.
- Security-sensitive changes (auth, injection guard, rate limit, input validation) belong in [tests/security/](tests/security/). Multi-step request flows go in [tests/integration/](tests/integration/).

### Touching endpoint contracts

When adding or changing a route: update `summary`, `description`, and `responses={...}` on the decorator; add / refresh the Pydantic `json_schema_extra` example; reflect the change in [API_CONTRACT.md](API_CONTRACT.md) (the curl + response snippets and the Laravel migration table). The three sources — code, Swagger, contract doc — should never drift.

### Seeding the knowledge base

The RAG corpus ships as curated Markdown under [data/knowledge_base/](data/knowledge_base/), with a declarative [manifest.json](data/knowledge_base/manifest.json) listing each article's `source_id`, language, topic, and public-domain attribution. Two CLIs in [scripts/](scripts/) drive the bulk load — both talk to a running service via `X-Service-Token`, so they work against local, Docker, or prod by swapping `--base-url`:

```bash
# Bulk-load all articles (idempotent: deletes by source_id then re-analyzes)
python scripts/seed_knowledge_base.py --manifest data/knowledge_base/manifest.json

# Verify corpus (chunk count, per-language coverage, relevance of canned queries)
python scripts/verify_knowledge_base.py
```

These scripts are offline tooling — excluded from the runtime image via [.dockerignore](.dockerignore). They hit two dev-only endpoints also gated by `ENABLE_DEV_ROUTES`: `GET /v1/rag/stats` (total + per-language counts) and `DELETE /v1/rag/source/{source_id}` (idempotent cleanup). Both are documented in [API_CONTRACT.md](API_CONTRACT.md) as dev-only, i.e. not part of the Laravel/Swift surface.

When adding articles: write the Markdown file, add a manifest entry (pick a stable human-readable `source_id`), list it in [data/knowledge_base/LICENSE.md](data/knowledge_base/LICENSE.md), then re-run the seeder. The Kazakh articles currently carry `review_status: "machine_translated"` in their manifest entries — clear the flag once they're reviewed.

## Deployment Notes

- **Dockerfile is multi-stage** (Python 3.11-slim). The builder stage installs deps with `build-essential`; the runtime stage ships only `curl` + the installed Python packages + `app/`. CMD is `gunicorn -k uvicorn.workers.UvicornWorker -w 4 --graceful-timeout 30`. The 30 s graceful timeout must stay aligned with `_SHUTDOWN_TIMEOUT` in [app/main.py](app/main.py) — don't change one without the other, or SSE streams get killed mid-flight.
- **Two compose files**: [docker-compose.yml](docker-compose.yml) is the dev stack (exposes Qdrant `6333` for inspection, uses `.env.docker`); [docker-compose.prod.yml](docker-compose.prod.yml) is an **overlay** (no port exposure for Redis/Qdrant, memory limits, json-file log rotation, `restart: always`, `.env.production`). Production command is `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` — running only the prod file standalone is wrong.
- **[.env.production](.env.production)** is a committed template with placeholders. Filled-in secrets must never be committed. `${REDIS_PASSWORD}` resolves from the deploy-host shell env, same pattern as the base compose file.
- `APP_ENV=production` is the master switch: `/docs` + `/redoc` 404, CORS refuses `*` and forces `allow_credentials=false`, the startup banner warns on wildcards. `ENABLE_DEV_ROUTES=false` unmounts `/v1/rag/*`.
- `/health` returns `status: "ok" | "degraded"` based on Redis, Qdrant, and the OpenAI circuit state — wire this to the orchestrator's liveness probe. The compose healthcheck already uses it.
- `/metrics` is unauthenticated; keep it behind a private network or add auth before exposing externally.
- Graceful shutdown waits up to 30 s for streaming connections registered via `register_stream` before closing Redis/Qdrant. gunicorn's `--graceful-timeout 30` respects this window.

## CI/CD

- **[.github/workflows/ci.yml](.github/workflows/ci.yml)** runs on every push and PR, four parallel jobs: `test` (pytest with the 80% coverage gate), `lint` (`ruff check` + `ruff format --check`), `typecheck` (mypy; `continue-on-error: true` — advisory, won't fail the build), and `security` (bandit `-lll` + pip-audit; pip-audit is also advisory). If you tighten mypy or pip-audit into hard gates, drop the `continue-on-error` flag in the same PR.
- **[.github/workflows/deploy.yml](.github/workflows/deploy.yml)** runs only on tags matching `v*`. It builds the multi-stage image, pushes to `ghcr.io/<owner>/<repo>` with semver + tag-ref labels, then runs a smoke test that boots the container with dummy env and polls `/health` for up to 20 s (HTTP 200 or 503 both pass — 503 means degraded-but-responsive, which is expected without real Redis/Qdrant).
- Cut a release by pushing a `vX.Y.Z` tag. No manual image pushes — the workflow is the only writer to GHCR.

## Tools
- Use the Context7 MCP tool to fetch up-to-date docs for FastAPI, OpenAI SDK, Qdrant, Redis, pydantic, etc. before guessing at API shapes.
