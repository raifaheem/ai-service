# Health AI Service — API Contract

This document is the authoritative HTTP contract between `health-ai-service` (Python/FastAPI)
and its consumers — the Laravel backend ([panacea](https://github.com/Antinikita/panacea))
and Swift clients. For the live, interactive schema, open `/docs` (Swagger UI) or `/redoc`
when running locally (both are disabled in production).

- **Base URL (dev):** `http://localhost:8001`
- **API version:** `v1` (prefixed on every endpoint).
- **Response headers:** every response carries `X-API-Version: v1`, `X-Service-Version: <semver>`, and `X-Request-Id`.

---

## 1. Authentication

There are two authentication modes. Every `/v1` endpoint accepts both.

### 1.1 Service-to-service (Laravel → Python)

```
X-Service-Token: <shared secret>
X-User-Id:       <end-user id, string>
```

- `SERVICE_TOKEN` in the service `.env` is **comma-separated** to support rotation.
  Any value in that set is accepted — deploy a new token, switch callers over, then drop the old one.
- `X-User-Id` is the downstream end-user identifier. The Python service treats it as opaque
  (string compare only), so Laravel's numeric auto-increment ids are fine. It is used for
  rate-limiting, conversation ownership, and context logging.

### 1.2 Direct client (Swift / SPA → Python)

```
Authorization: Bearer <jwt>
```

- **RS256 only.** The algorithm is hardcoded in [security.py](app/security.py); HS256 tokens are rejected even if the public key is supplied as a "shared secret" (defense against the classic alg-confusion attack).
- Required claims: `sub` (becomes the user id), `exp`.
- `aud` (audience) and `iss` (issuer) are pinned **when configured** via `JWT_AUDIENCE` / `JWT_ISSUER` env vars. Tokens with mismatched or missing claims are rejected. In production these env vars are required when `JWT_PUBLIC_KEY` is set — without them, tokens minted for a different consumer of the same key would be accepted.
- `X-User-Id` is **ignored** when a valid JWT is present.

### 1.3 Errors

| Status | Condition |
| --- | --- |
| `401 Unauthorized` | No credentials or invalid token / JWT. |
| `401 Unauthorized` | JWT present but `sub` claim missing. |
| `400 Bad Request`  | Service auth succeeded but `X-User-Id` header missing. |

---

## 2. Endpoints

### 2.1 `POST /v1/chat` — synchronous consultation

Send a message, get a complete answer. Runs auth → rate limit → injection guard → intent classification → RAG → LLM → content safety → Redis persistence.

**Request**

```http
POST /v1/chat HTTP/1.1
Host: localhost:8001
Content-Type: application/json
X-Service-Token: <token>
X-User-Id: 42

{
  "message": "У меня болит голова уже 3 дня, что делать?",
  "locale": "ru",
  "profile": {
    "age": 30,
    "sex": "female",
    "goals": ["immunity"]
  },
  "conversation_id": "c3a1b2d4-5678-4abc-9def-0123456789ab"
}
```

**Response `200 OK`**

```json
{
  "answer": "Головная боль в течение 3 дней может быть связана с… Рекомендую проконсультироваться с врачом.",
  "disclaimer": "Эта информация носит справочный характер и не заменяет консультацию врача.",
  "conversation_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
  "rag_used": true,
  "rag_score": 0.82,
  "sources": [
    {
      "source_id": "article-headache-guide-2024",
      "title": "Headache management — clinical guidelines",
      "language": "ru",
      "score": 0.82
    }
  ],
  "intent": {
    "category": "symptom_check",
    "risk_level": "medium",
    "confidence": 0.88
  }
}
```

**curl**

```bash
curl -X POST http://localhost:8001/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Service-Token: $AI_SERVICE_TOKEN" \
  -H "X-User-Id: 42" \
  -d '{"message":"У меня болит голова 3 дня","locale":"ru"}'
```

**Request schema — key fields**

| Field | Type | Constraint | Notes |
| --- | --- | --- | --- |
| `message` | string | 1–4000 chars, **required** | The user's message. |
| `locale` | string | `ru` \| `en` \| `kk` | Unknown values fall back to `ru`. |
| `profile` | object | optional | See `UserProfile` — personalization fields. |
| `conversation_id` | string | UUID v4, optional | Server generates one when omitted. Ownership is locked on first write. |
| `history` | array | optional | When provided, replaces server-stored history. The summarizer (>8 turns → summary + last 6) decides what to keep regardless of source. |
| `metadata` | object | ≤ 5 KB JSON | Free-form client metadata. |
| `region` | string | ISO 3166-1 alpha-2, optional | Used to localize the emergency-phone number on emergency-intent replies (e.g. `KZ` → `112 / 103`, `US` → `911`, `GB` → `999`). Unknown/omitted values fall back to a locale-specific default. |
| `idempotency_key` | string | ≤ 64 chars, optional | Scoped per `(user_id, key)`. A repeat with the same key **and** the same body returns the cached response (10-min TTL). A repeat with the same key but a **different** body fingerprint returns **409 Conflict**. Fingerprint composition: `sha256(message + \x1f + conversation_id + \x1f + locale + \x1f + region)`. Sync `/v1/chat` only. Cache replays are recorded in the audit log as `chat.answer` events with `cached=true`. |

---

### 2.2 `POST /v1/chat/stream` — streaming consultation (SSE)

Same pipeline as `/v1/chat` but emits `Server-Sent Events` (`text/event-stream`).

**Event sequence** — every event payload includes `request_id` (echoes the response header) so support tickets can cite a single id.

| Event | Fired | Payload |
| --- | --- | --- |
| `meta`  | exactly once, first      | `{"request_id": "...", "conversation_id": "<uuid>"}` |
| `delta` | zero or more             | `{"request_id": "...", "text": "<partial text>"}` |
| `final` | once on success          | `{request_id, conversation_id, answer, disclaimer, model, finish_reason, usage, rag_used, rag_score, sources, intent}` |
| `error` | replaces `final` on failure | `{request_id, conversation_id, code, message}` |

**SSE error codes** (`code` field inside an `error` event). The `message` field is intentionally generic ("Upstream service unavailable.") for upstream errors so we don't leak provider-side details to clients — branch on `code`, not `message`.

| Code | Cause |
| --- | --- |
| `openai_rate_limit` | Upstream 429 (quota / billing). |
| `openai_auth` | Upstream authentication failure. |
| `openai_connection` | Network error reaching upstream. |
| `openai_api_status` | Upstream returned a non-2xx HTTP status. |
| `service_degraded` | OpenAI circuit breaker open (3+ failures in 60 s window). |
| `internal_error` | Unhandled exception in the streaming handler. |

**Sample stream body**

```
event: meta
data: {"conversation_id":"c3a1b2d4-5678-4abc-9def-0123456789ab"}

event: delta
data: {"text":"Головная "}

event: delta
data: {"text":"боль..."}

event: final
data: {"conversation_id":"c3a1b2d4-...","answer":"...","disclaimer":"...","usage":{"prompt_tokens":410,"completion_tokens":120},"rag_used":true,"sources":[...],"intent":{"category":"symptom_check","risk_level":"medium","confidence":0.88}}
```

Response headers always include `Cache-Control: no-cache` and `X-Accel-Buffering: no` to prevent proxy buffering.

---

### 2.3 `GET /v1/conversations/{conversation_id}`

Returns all stored turns plus Redis TTL. Caller must own the conversation.

```bash
curl http://localhost:8001/v1/conversations/c3a1b2d4-... \
  -H "X-Service-Token: $AI_SERVICE_TOKEN" \
  -H "X-User-Id: 42"
```

```json
{
  "conversation_id": "c3a1b2d4-...",
  "ttl_seconds": 3600,
  "turns": [
    {"role": "user", "content": "...", "ts": 1718280000},
    {"role": "assistant", "content": "...", "ts": 1718280001}
  ]
}
```

### 2.4 `GET /v1/conversations/{conversation_id}/metadata`

Returns stored metadata (topic, turn_count, …) + TTL.

### 2.5 `DELETE /v1/conversations/{conversation_id}`

Deletes turns, summary, owner, and metadata. Returns `{"deleted": true, "conversation_id": "..."}`.

---

### 2.6 `POST /v1/articles/analyze`

Chunk, index, and analyze a medical article from a JSON body. Both article endpoints require `X-User-Id` (or a JWT with `sub`) — they share the chat rate-limit bucket per user. `text` is capped at 200 000 characters.

```bash
curl -X POST http://localhost:8001/v1/articles/analyze \
  -H "Content-Type: application/json" \
  -H "X-Service-Token: $AI_SERVICE_TOKEN" \
  -H "X-User-Id: 42" \
  -d '{
        "title": "Headache management guidelines",
        "text": "Primary headaches include migraine, tension-type...",
        "language": "en",
        "index_chunks": true
      }'
```

Response: `ArticleAnalysisResponse` with `summary`, `key_findings`, `limitations`, `practical_meaning`, `red_flags`, `confidence`, plus `indexed_chunks` counter.

### 2.7 `POST /v1/articles/analyze-file`

Same as above but accepts a `.txt` / `.pdf` / `.docx` upload via `multipart/form-data` (max 10 MB).

---

### 2.8 `GET /health` — liveness

```json
{
  "status": "ok",
  "env": "dev",
  "version": "1.0.0",
  "checks": {
    "redis": "ok",
    "qdrant": "ok",
    "openai_circuit": "closed",
    "qdrant_circuit": "closed"
  }
}
```

`status` is `"degraded"` when any dependency is unavailable or the OpenAI circuit is open. Wire this to the orchestrator's liveness probe.

### 2.9 `GET /metrics` — Prometheus metrics

**Authenticated** (same `X-Service-Token` or `Authorization: Bearer <jwt>` as the other routes). Returns Prometheus text format (`Content-Type: text/plain; version=0.0.4`) with per-worker shards aggregated via `prometheus_client.multiprocess`. Exposes `healthai_requests_total`, `healthai_request_duration_seconds`, `healthai_intent_total`, `healthai_openai_tokens_total`, `healthai_rag_requests_total`, `healthai_circuit_breaker_state`, `healthai_active_conversations`, `healthai_qdrant_collection_size`. In-cluster Prometheus scrapers should pull `X-Service-Token` from a Kubernetes secret.

### 2.10 Dev-only RAG management (`ENABLE_DEV_ROUTES=true`)

The routes below are exposed only when `ENABLE_DEV_ROUTES=true` and are **not part of the Laravel/Swift contract** — they exist so offline tooling ([scripts/seed_knowledge_base.py](scripts/seed_knowledge_base.py) and [scripts/verify_knowledge_base.py](scripts/verify_knowledge_base.py)) can bulk-load and audit the RAG corpus. All require `X-Service-Token`. In production (`APP_ENV=production` with `ENABLE_DEV_ROUTES=false`) they return 404.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/rag/index` | Upsert a raw text chunk with language/metadata (used by seeder via `/v1/articles/analyze` in practice). |
| `POST` | `/v1/rag/search` | Retrieve top-K chunks for a query string; used by the verifier's relevance checks. |
| `GET` | `/v1/rag/stats` | Returns `{total, by_language, collection}` for the active Qdrant collection. |
| `DELETE` | `/v1/rag/source/{source_id}` | Deletes all chunks whose payload has a matching `source_id` (idempotent reseeding). Returns `{source_id, deleted}`. |

These endpoints are intentionally omitted from the Laravel migration table in §8.3 — downstream consumers should never depend on them.

---

### 2.11 Pre-consultation triage — `POST /v1/triage/session`

Server-driven symptom intake: a fixed 10-step form that collects the chief complaint, onset, trajectory, severity (0–10), accompanying symptoms, triggers, relevant history, current medications, allergies, and an explicit red-flag screen. The server owns the step sequence; the client just forwards the user's free-text answer each turn. Output is a clinician-facing JSON report with a specialist routing recommendation from a closed enum. Free-text answers are run through the same prompt-injection guard as `/v1/chat`; on every red-flag step the server *also* runs a deterministic keyword scan (`chest pain`, `loss of consciousness`, `боль в груди`, …) so the LLM cannot be talked out of detecting an emergency.

**Start (no `session_id`)**

```http
POST /v1/triage/session HTTP/1.1
Content-Type: application/json
X-Service-Token: <token>
X-User-Id: 42

{"locale": "en", "region": "KZ"}
```

Response:

```json
{
  "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
  "state": "in_progress",
  "step_index": 0,
  "total_steps": 10,
  "next_step": {
    "step_id": "primary_complaint",
    "question": "I will ask a few short questions to prepare a summary for your clinician. What brings you in today? …",
    "kind": "free_text"
  },
  "disclaimer": "This is not a medical diagnosis and does not replace consultation with a doctor."
}
```

**Advance (`session_id` + `answer`)**

```http
POST /v1/triage/session HTTP/1.1
Content-Type: application/json
X-Service-Token: <token>
X-User-Id: 42

{
  "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
  "answer": "Pulsating headache on the right side, started about three days ago.",
  "locale": "en",
  "region": "KZ"
}
```

Response shapes by `state`:

- `in_progress` → `next_step: {step_id, question, kind, choices?, range?, clarification?}`. `clarification` is set when the server wants the user to restate (max 2 per step, then force-accepted as unparsed). The injection-guard refusal is also delivered as a `clarification` without advancing the step index.
- `completed` → `report: {clinical_summary, structured, specialist_recommendation: {category, rationale}, detected_red_flags}`. `category` is one of `gp, emergency_room, urgent_care, cardiologist, neurologist, gastroenterologist, dermatologist, endocrinologist, pulmonologist, psychiatrist, gynecologist, urologist, orthopedist, otolaryngologist`; out-of-enum values fall back to `gp`.
- `red_flag_exit` → `emergency_message` (locale-specific), `detected_red_flag`, `emergency_phone`. The phone is resolved via [D.1 regionalization](#) — `region=KZ` → `"112 / 103"`, `region=US` → `"911"`, unknown region → locale-neutral default. `detected_red_flag` carries the LLM's reason or `keyword:<pattern>` when the deterministic scan caught it.

**`completed` example**

```json
{
  "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
  "state": "completed",
  "step_index": 9,
  "total_steps": 10,
  "report": {
    "clinical_summary": "Patient reports a 3-day pulsating right-sided headache, severity 6/10, worsening trajectory. No nausea or photophobia. No prior history of migraine. Currently on no medications, no allergies.",
    "structured": {
      "primary_complaint": "right-sided headache",
      "onset": "3 days ago",
      "trajectory": "worsening",
      "severity": 6,
      "accompanying": "none",
      "triggers": "screen time",
      "relevant_history": "none",
      "current_meds": "none",
      "allergies": "none",
      "explicit_red_flags": false
    },
    "specialist_recommendation": {
      "category": "neurologist",
      "rationale": "Recurrent localized headache pattern with worsening trajectory warrants neurological review."
    },
    "detected_red_flags": []
  },
  "disclaimer": "This is not a medical diagnosis and does not replace consultation with a doctor."
}
```

**Request schema**

| Field | Type | Constraint | Notes |
| --- | --- | --- | --- |
| `session_id` | string | UUID v4, optional | Omit on first request — server creates one. |
| `answer` | string | 1–2000 chars | Required when `session_id` is supplied. |
| `locale` | string | `ru` / `en` / `kk` | Unknown values fold to `ru`. |
| `region` | string | ISO 3166-1 alpha-2, optional | Only used if a red flag fires — picks the emergency phone. |

**Session step table**

| Index | step_id | kind | Red-flag check |
| --- | --- | --- | --- |
| 0 | `primary_complaint` | free_text | yes |
| 1 | `onset` | free_text | — |
| 2 | `trajectory` | choice (worsening/stable/improving) | — |
| 3 | `severity` | int_scale (0–10) | — |
| 4 | `accompanying` | free_text | yes |
| 5 | `triggers` | free_text | — |
| 6 | `relevant_history` | free_text | — |
| 7 | `current_meds` | free_text | — |
| 8 | `allergies` | free_text | — |
| 9 | `explicit_red_flags` | boolean | yes |

**Status codes**

| Status | When |
| --- | --- |
| `400` | `answer` missing when `session_id` supplied. |
| `403` | Session belongs to a different user. |
| `404` | Session not found or expired. |
| `409` | Session already terminated (`red_flag_exit` or `completed`) — start a new one. **Also returned when a concurrent advance landed first** (CAS on `version`); reload the session and retry. |
| `429` | Rate limit exceeded (same per-user bucket as `/v1/chat`). |

**Recovery and cancel**

- `GET /v1/triage/session/{session_id}` — returns `{session_id, state, step_index, total_steps, locale, region, created_at, updated_at, ttl_seconds}`. Does NOT return collected answers (by design — those belong in the final report and in the audit log).
- `DELETE /v1/triage/session/{session_id}` — idempotent abandon. Clears Redis state; 404 if nothing existed.

---

## 3. HTTP error codes

| Status | When |
| --- | --- |
| `400` | Input validation failed (message > 4000 chars, metadata > 5 KB, empty query, unsupported file type, extraction error, missing `X-User-Id` on service auth). |
| `401` | Missing or invalid authentication. |
| `403` | `conversation_id` belongs to a different user. |
| `404` | Conversation / metadata not found or expired. |
| `409` | `idempotency_key` reused with a different request body (`/v1/chat`); concurrent triage advance landed first (`/v1/triage/session`); session already terminated. |
| `413` | File upload exceeds 10 MB. |
| `422` | Pydantic validation error (malformed JSON, wrong types, `text` over `max_length`). |
| `429` | Rate limit exceeded (`RATE_LIMIT_PER_MINUTE` + `RATE_LIMIT_BURST`). |
| `502` | Upstream failure (OpenAI auth / connection / API status, article LLM analysis failure). |
| `503` | Service degraded (circuit breaker open, OpenAI quota exhausted). |

Error payloads always look like:

```json
{ "detail": "human-readable reason" }
```

The `X-Request-Id` response header is always present — include it when reporting incidents.

---

## 4. Response headers

| Header | Value | Origin |
| --- | --- | --- |
| `X-API-Version` | `v1` | `APIVersionMiddleware` |
| `X-Service-Version` | service `app_version` (semver) | `APIVersionMiddleware` |
| `X-Request-Id` | per-request UUID (echoes `X-Request-Id` if caller sent one) | `RequestLoggingMiddleware` |

---

## 5. Limits

| Limit | Default | Source |
| --- | --- | --- |
| Message length | 4000 chars | `ChatRequest.message` |
| Metadata size | 5 KB (JSON-serialized) | `ChatRequest.metadata` |
| Profile list items | 20 items × 200 chars | `UserProfile.*` |
| History per request | unbounded; summarizer caps LLM context (>8 turns → summary + last 6) | `chat.py`, `summarizer.py` |
| Rate limit | `RATE_LIMIT_PER_MINUTE` + `RATE_LIMIT_BURST` (25 default) per user per minute (sliding window) | `rate_limit.py` |
| Redis turn retention | `REDIS_MAX_TURNS` (12 default) | `memory.py` |
| Redis TTL | `REDIS_TTL_SECONDS` | `memory.py` |
| Idempotency cache TTL | 10 min | `memory.py` |
| Article text length | 200 000 chars | `ArticleAnalyzeRequest.text` |
| Article upload size | 10 MB | `articles.py` |
| RAG dev `/v1/rag/index` chunk text | 8000 chars per chunk, 200 chunks per request | `schemas_rag.py` |

---

## 6. Conversation semantics

- `conversation_id` is a **UUIDv4**, ideally generated by the client. Omit it on the first request and the server will return one in the response.
- Ownership is locked on the first successful write via Redis `SET NX` — a second user sending the same `conversation_id` gets `403`.
- Redis TTL is refreshed on every append. A conversation silently expires after `REDIS_TTL_SECONDS` of inactivity.
- When history exceeds 8 turns, the older portion is summarized and replaced; the summary is refreshed every `RESUMMARIZE_AFTER_N_TURNS` (6) new turns so it doesn't freeze at the moment of first creation.

### `sources[]` schema

Each `ChatSource` carries `source_id`, `title`, `language`, `score`. When the chunk was retrieved via cross-language fallback (request locale had too few hits and the search was rerun without a language filter), an `is_fallback: true` field is added — clients can use this to mark such results visually. In-language hits omit the field entirely.

---

## 7. Versioning policy

- The current prefix is `/v1`. All breaking wire-format changes will ship under `/v2`, with `/v1` kept for **one release cycle** for overlap.
- `X-API-Version` is a convenience header — it mirrors the prefix in the URL.
- `X-Service-Version` is the deployed semver; it changes on every service release and is safe for version skew alerting.

---

## 8. Laravel integration

### 8.1 Required env vars (Laravel side)

```env
AI_MODULE_URL=http://ai-service:8001
AI_SERVICE_TOKEN=<matches SERVICE_TOKEN value in Python .env>
```

Add a matching `services` entry:

```php
// config/services.php
'ai_module' => [
    'url'   => env('AI_MODULE_URL'),
    'token' => env('AI_SERVICE_TOKEN'),
],
```

### 8.2 Minimal Guzzle / `Http::` example

```php
use Illuminate\Support\Facades\Http;

$response = Http::withHeaders([
        'X-Service-Token' => config('services.ai_module.token'),
        'X-User-Id'       => (string) $user->id,
    ])
    ->timeout(60)
    ->acceptJson()
    ->post(config('services.ai_module.url') . '/v1/chat', [
        'message' => $request->input('message'),
        'locale'  => $user->preferred_locale ?? 'ru',
        'profile' => [
            'age'  => $user->age,
            'sex'  => $user->sex,
            'goals' => $user->health_goals ?? [],
        ],
        'conversation_id' => $request->input('conversation_id'),
    ]);

if ($response->failed()) {
    Log::warning('AI service call failed', [
        'status'      => $response->status(),
        'request_id'  => $response->header('X-Request-Id'),
        'body'        => $response->body(),
    ]);
    abort(502, 'AI service unavailable');
}

$data = $response->json();
// $data['answer'], $data['disclaimer'], $data['conversation_id'], $data['sources'], ...
```

### 8.3 Migration notes — `ComplaintAIController.php`

The existing controller (currently behind `$useMock = true`) sends a payload shape that does **not** match this contract. When flipping the mock off, apply these changes:

| Current (mock) | Replace with |
| --- | --- |
| `user_id` inside the JSON body | `X-User-Id` **header** (string) |
| `context: {age, sex, goals, metrics}` | `profile: {age, sex, goals, ...}` — matches `UserProfile` |
| `mode: "general"` | **remove** — intent is classified server-side; use `profile.goals` for hints |
| Response field `reply` / `response` / `message` | Response field **`answer`** |
| No conversation id | Include `conversation_id` (client-generated UUIDv4) to get history continuity |

### 8.4 Streaming from Laravel

SSE is best consumed directly by the client (Swift, browser `EventSource`). If Laravel must proxy:

```php
use Illuminate\Support\Facades\Http;

$response = Http::withHeaders([...])
    ->withOptions(['stream' => true])
    ->post(config('services.ai_module.url') . '/v1/chat/stream', [...]);

return response()->stream(function () use ($response) {
    $body = $response->toPsrResponse()->getBody();
    while (!$body->eof()) {
        echo $body->read(1024);
        ob_flush(); flush();
    }
}, 200, [
    'Content-Type'      => 'text/event-stream',
    'Cache-Control'     => 'no-cache',
    'X-Accel-Buffering' => 'no',
]);
```

---

## 9. Swift / mobile note

`URLSession` with a custom `URLSessionDataDelegate` parses `text/event-stream` natively. Break received bytes on the `\n\n` boundary, then split the event name (`event: <name>`) from the JSON payload (`data: {...}`). Handle the `error` event type as a terminal state — no `final` event will follow.
