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

### `/v1/triage/session` — pre-consultation triage ([app/routers/triage.py](app/routers/triage.py))

Server-driven symptom intake — a fixed 10-step form ([app/services/triage.py](app/services/triage.py) `TRIAGE_FORM`). Each POST advances one step: the router calls `normalize_answer` (one LLM JSON-response call) to map the user's free text to a structured value + red-flag signal, then `advance()` mutates the session. After the last step a second LLM call (`build_report`) emits the clinician-facing report — `{clinical_summary, structured, specialist_recommendation, detected_red_flags}`. `specialist_recommendation.category` is a closed enum (see [app/prompts_triage.py](app/prompts_triage.py) `SPECIALIST_CATEGORIES`); out-of-enum values fall back to `gp`. Red flags short-circuit the session at any step and emit an emergency_phone resolved via [D.1 `get_emergency_phone`](app/services/i18n.py). State persists in Redis under `healthai:triage:{session_id}:{state,owner}` ([app/services/triage_memory.py](app/services/triage_memory.py)); same TTL + owner-first-writer-wins pattern as chat conversations. No SSE — Q&A is short, streaming isn't needed.

### `/v1/chat` + `/v1/chat/stream` pipeline ([app/routers/chat.py](app/routers/chat.py))

Same pipeline, JSON vs SSE (`meta`/`delta`/`final`/`error`; every SSE event carries `request_id`):
1. Auth ([security.py](app/security.py)) — JWT RS256 (with optional `aud`/`iss` pinning) or `X-Service-Token` + `X-User-Id`.
2. **Idempotency check (C.4 + A5)** — when `idempotency_key` is supplied, fingerprint = `sha256(message + conversation_id + locale + region)`. Match → return cached response (and emit `chat.answer` audit with `cached=true`). Different fingerprint under the same key → **409 Conflict**.
3. Rate limit ([rate_limit.py](app/services/rate_limit.py)) — per-user sliding window via Redis zset; cap = `RATE_LIMIT_PER_MINUTE + RATE_LIMIT_BURST`.
4. Owner check — `conversation_id` owner in Redis must match user_id (403 else). **Fails closed.**
5. Prompt-injection guard ([safety.py](app/services/safety.py)) — regex; refusal skips LLM and emits `chat.injection_blocked` audit.
6. History — from body (forwarded as-is) or Redis (up to `REDIS_MAX_TURNS`). Summarizer downstream caps it.
7. Intent ([intent.py](app/services/intent.py)) — cheap LLM call (tracing span `intent.classify`), cached 300s. `off_topic` with confidence ≥0.7 short-circuits.
8. Summarization ([summarizer.py](app/services/summarizer.py)) — >8 turns → summary + last 6 (summary *replaces* older history, doesn't prepend).
9. RAG ([rag.py](app/services/rag.py), [vector_store.py](app/services/vector_store.py), tracing span `rag.build`) — cached embedding, Qdrant w/ language filter, drop below `RAG_SCORE_THRESHOLD`, cross-language fallback (chunks gain `is_fallback=true`). **Fails open**.
10. Circuit breakers ([circuit_breaker.py](app/services/circuit_breaker.py), HASH-backed atomic state via HINCRBY/HSET) — `openai_breaker` wraps **every** OpenAI call (intent, summarizer, RAG embed, LLM, triage, article analyzer) via [openai_call_guard.py](app/services/openai_call_guard.py); `qdrant_breaker` wraps every search/upsert via [breaker_guard.py](app/services/breaker_guard.py). **Both fail closed**: open after 3 failures/60s → 503 with degraded message.
11. LLM ([llm.py](app/services/llm.py), tracing span `llm.generate`) — system + locale addon (via `intent.addon_name`) + summary + RAG + history. Temperature from `CATEGORY_TO_TEMPERATURE`.
12. Content filter ([content_filter.py](app/services/content_filter.py)) — softens diagnoses, appends doctor note near drug dosages.
13. Persist + audit — Redis `RPUSH`/`LTRIM` to `REDIS_MAX_TURNS`, owner via `SET NX` (first writer wins). Every successful answer emits a `chat.answer` audit event ([app/services/audit.py](app/services/audit.py)).

### Singletons & lifespan

[app/main.py](app/main.py) lifespan initializes Redis/Qdrant/OpenAI singletons and calls `ensure_qdrant_collection` (**refuses to start on vector-size mismatch** — recreate collection or set new `QDRANT_COLLECTION`). Use `get_redis()`/`get_qdrant()`; never instantiate `AsyncOpenAI` directly. Graceful shutdown waits 30s for streams registered via `register_stream` — must stay aligned with Dockerfile's `--graceful-timeout 30`.

### Redis keys (prefix `REDIS_PREFIX`, default `healthai`)

- `conv:{id}:{turns,owner,summary,summary_meta,meta}` — chat state. `summary_meta` is JSON `{turn_count_at_summary}` so resummarization fires after `RESUMMARIZE_AFTER_N_TURNS` new turns instead of freezing at first summary.
- `rl:{user_id}` — sliding-window rate limiter (Redis zset of `epoch_ms:uuid` members, 60s TTL on set).
- `emb:{model}:{md5}` — embedding cache. **Includes the model in the key** — flipping `OPENAI_EMBEDDING_MODEL` invalidates the cache automatically.
- `intent:v2:{md5}` — intent classifier cache. The `v2` prefix lets us bump the cache when classify-prompt or schema changes.
- `idem:{user_id}:{key}` — idempotency cache. Stores `{"fingerprint": "...", "response": {...}}`; 10-min TTL. Mismatched fingerprint under the same key → 409.
- `triage:{session_id}:{state,owner}` — triage state (D.3.a). State is a JSON blob carrying a `version` field for optimistic CAS in `save_session`.
- `cb:{name}` — distributed circuit-breaker HASH `{state, failure_count, last_failure_time}`. `name` is `openai` or `qdrant`. 5-min TTL on writes.
- `audit` — Redis Stream of audit events (MAXLEN ~1M). Identifiers + metadata only, never message content.

### Config knobs ([app/config.py](app/config.py), pydantic-settings)

- `APP_ENV=production` — disables `/docs`+`/redoc`, requires `JWT_AUDIENCE`+`JWT_ISSUER` when `JWT_PUBLIC_KEY` is set, rejects `ALLOWED_ORIGINS=*` outright (raises at boot via `_validate_prod_safety`), rejects `redis://` URLs without password and placeholder `SERVICE_TOKEN`s.
- `ENABLE_DEV_ROUTES` — mounts `/v1/rag/*` (keep false in prod; production-safety rejects true).
- `RAG_SCORE_THRESHOLD` (0.35) — too high = "RAG silently went cold".
- `OPENAI_MAX_RETRIES` (3), `OPENAI_TIMEOUT_SECONDS` (30), `MAX_RESPONSE_TOKENS` (1000).
- `OPENAI_EMBEDDING_MODEL` — flipping this invalidates the embedding cache automatically (model is part of the cache key).
- `EMBEDDING_CACHE_TTL` (86400 / 24h).
- `JWT_PUBLIC_KEY` — RS256 PEM. Algorithm is **hardcoded** to RS256 (no `JWT_ALG` knob anymore — defense against an HS256-with-pubkey-as-secret attack).
- `JWT_AUDIENCE`, `JWT_ISSUER` — optional in dev, required in prod when `JWT_PUBLIC_KEY` is set; pinned in `jwt.decode`.
- `QDRANT_TIMEOUT` (10).
- `REDIS_MAX_CONNECTIONS` (20), `REDIS_SOCKET_TIMEOUT` (5).
- `LOG_FORMAT=json|text`.

## Development Patterns

- **Prompts** ([app/prompts.py](app/prompts.py)) — `SYSTEM_PROMPTS`, `DISCLAIMERS`, `ADDON_PROMPTS` are all keyed by `ru`/`en`/`kk`. Always update all three; unknown locales fold to `ru` (no error).
- **New intent category**: add to `VALID_CATEGORIES`, `CATEGORY_TO_TEMPERATURE`, `CATEGORY_TO_ADDON` in [intent.py](app/services/intent.py); extend `CLASSIFY_SYSTEM_PROMPT`; add locale addon in [prompts.py](app/prompts.py) if needed.
- **RAG chunks**: every chunk's `payload.language` must be set — search filter relies on it.
- **Endpoint contracts**: when changing a route, update decorator `summary`/`description`/`responses`, Pydantic `json_schema_extra` example, and [API_CONTRACT.md](API_CONTRACT.md) — all three must stay in sync.
- **Service token rotation**: `SERVICE_TOKEN` is comma-separated; any listed token is valid.
- **Dev UI**: `http://localhost:8001/dev-ui` — single-page chat console for manual LLM behavior testing (SSE streaming, conversation memory, intent/RAG/finish_reason metadata for screenshots). Gated behind `ENABLE_DEV_ROUTES=true` and **never mounted in prod**. Source: [app/dev_ui/index.html](app/dev_ui/index.html), router: [app/routers/dev_ui.py](app/routers/dev_ui.py). The dev compose file bind-mounts `./app:/app/app:ro` so HTML edits land on browser refresh without `up --build`.
- **PII / logging**: medical content (user messages, profile details, conversation turns, LLM answers) MUST NOT appear in application logs. Only log identifiers (`request_id`, `conversation_id`, `user_id`, `intent.category`) and metadata (`duration_ms`, token counts). A `PIIRedactorFilter` in [logging_config.py](app/logging_config.py) catches accidental leaks via `extra={"user_message": ...}` and friends — extend `_REDACT_KEYS` if new PII fields appear. For debugging that needs payload context, prefer tracing spans (which don't persist by default) over logs.

### Knowledge base

Curated Markdown under [data/knowledge_base/](data/knowledge_base/) + [manifest.json](data/knowledge_base/manifest.json). Seed via `python scripts/seed_knowledge_base.py --manifest data/knowledge_base/manifest.json` (idempotent); verify via `python scripts/verify_knowledge_base.py`. Both hit dev-only endpoints (`ENABLE_DEV_ROUTES=true`) via `X-Service-Token`. When adding articles, also update [data/knowledge_base/LICENSE.md](data/knowledge_base/LICENSE.md).

## Deployment

- Multi-stage Dockerfile; runtime CMD is `gunicorn -k uvicorn.workers.UvicornWorker -w 4 --graceful-timeout 30`. Image is **multi-arch** (linux/amd64 + linux/arm64) so prod can run on Oracle Cloud Free Tier (Ampere A1 ARM).
- Prod = **overlay**: `docker-compose.yml` + `docker-compose.prod.yml` (not the prod file alone). `.env.production` is deploy-host only (gitignored). The prod overlay adds Caddy (reverse proxy + auto Let's Encrypt), Prometheus, Grafana, Alertmanager, and alertmanager-bot (Telegram bridge) — see [docker-compose.prod.yml](docker-compose.prod.yml). Image source is `ghcr.io/raifaheem/ai-service:${IMAGE_TAG}`; the deploy job exports `IMAGE_TAG=<tag>` before `docker compose pull/up`.
- Resource budget in prod overlay: redis 0.5 CPU / 512M, qdrant 1.5 CPU / 2G, ai 2.0 CPU / 1G, caddy 0.3/128M, prometheus 0.5/512M, grafana 0.3/256M, alertmanager 0.2/128M, alertmanager-bot 0.1/64M. Total ≈ 5.4 CPU / 5G — fits comfortably in the Ampere A1 free tier (4 OCPU / 24GB).
- `/health` → `ok|degraded` (Redis + Qdrant + circuit state); Caddy proxies it but the systemd unit also polls localhost:8001 for the post-deploy health gate.
- `/metrics` is **authenticated** — accepts `X-Service-Token`, JWT, OR `Authorization: Bearer $METRICS_SCRAPE_TOKEN` (the last specifically for Prometheus, which can't send custom headers in scrape_configs). Rotate `METRICS_SCRAPE_TOKEN` separately from `SERVICE_TOKEN` so Laravel rotation doesn't break scrapes. See [app/main.py](app/main.py) `_metrics_auth`.
- Release: push `vX.Y.Z` tag → [.github/workflows/deploy.yml](.github/workflows/deploy.yml) builds multi-arch + pushes to GHCR + SSHes into the Oracle VM and runs `git checkout $TAG && docker compose pull && up -d`, then polls `/health` (fails the CI run if status≠ok in 60s). Manual redeploy: `workflow_dispatch` with the tag name. CI = [.github/workflows/ci.yml](.github/workflows/ci.yml) (test/lint/typecheck/security).
- **Three deploy paths**, all with auto-skipping CI jobs gated on which secret is set:
  - (1) [docs/DEPLOY.md](docs/DEPLOY.md) — **VPS** (Oracle/Hetzner Ubuntu VM, SSH-deploy from CI, systemd autostart, full compose stack incl. Caddy/Prometheus/Grafana/Alertmanager).
  - (2) [docs/SELFHOST.md](docs/SELFHOST.md) — **self-host on Windows PC** with Docker Desktop; CI only builds, **Watchtower** on the host polls GHCR every 60s and auto-updates the `ai` container.
  - (3) [docs/FLY.md](docs/FLY.md) — **Fly.io** + managed Redis (Upstash free) + managed Qdrant (Qdrant Cloud free). Single-service Fly app driven by [fly.toml](fly.toml); no compose. CI does `flyctl deploy --image ghcr.io/...:tag` reusing the GHCR-built image (no double build).
  - CI gates: `deploy` job runs only if `SSH_HOST` secret is set; `fly-deploy` only if `FLY_API_TOKEN` is set. Same `git tag v*` triggers whichever is configured.
- **On-call runbook:** [docs/RUNBOOK.md](docs/RUNBOOK.md) — 11 scenarios (OpenAI down, Redis OOM, Qdrant restore, rate-limit storm, JWT rotation, startup failures, chat stream hangs, **rollback**, **Caddy TLS failures**, **Telegram alerts not arriving**).

### Backup policy

**Redis is intentionally ephemeral.** Conversation turns, rate-limit counters, summary metadata, idempotency cache, triage sessions, and circuit-breaker state all live there with TTL. AOF (`docker-compose.yml`) protects against container restart but not host loss; we accept that exposure because every Redis-resident value is recoverable: a stuck client starts a new conversation, rate-limit counters expire in 60s, triage sessions abandon naturally on TTL. If you change this stance (e.g. compliance retention requirements), add a `redis-backup` compose profile mirroring the Qdrant one rather than relying on AOF alone.

**Qdrant is the only non-regenerable asset** — it holds the embedded knowledge base. Snapshot via the `backup` profile:

```bash
docker compose --profile backup run --rm qdrant-backup
```

That runs [scripts/qdrant_backup.py](scripts/qdrant_backup.py) which POSTs to Qdrant's snapshot API, downloads the new snapshot file to `./backups/qdrant/` (mounted read-write), and prunes everything older than the 7 most recent. Wire to host cron daily. Off-host transport (S3 / rclone / borg) is intentionally out of scope — point your existing backup runner at `./backups/qdrant/`.

To restore: `POST /collections/{collection}/snapshots/upload` with the snapshot file, or use `recover_snapshot_from_uri` from the qdrant-client Python API.

## Tools

Use the Context7 MCP tool for FastAPI, OpenAI SDK, Qdrant, Redis, pydantic docs before guessing at API shapes.
