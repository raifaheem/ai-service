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

- RS256-signed, verified against `JWT_PUBLIC_KEY` in the service `.env`.
- Required claim: `sub` (becomes the user id). `exp` is honored.
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
| `history` | array | optional | When provided, replaces server-stored history (trimmed to last 8). |
| `metadata` | object | ≤ 5 KB JSON | Free-form client metadata. |
| `region` | string | ISO 3166-1 alpha-2, optional | Used to localize the emergency-phone number on emergency-intent replies (e.g. `KZ` → `112 / 103`, `US` → `911`, `GB` → `999`). Unknown/omitted values fall back to a locale-specific default. |
| `idempotency_key` | string | ≤ 64 chars, optional | Scoped per user. A repeat request within 10 min returns the cached response. Sync `/v1/chat` only. |

---

### 2.2 `POST /v1/chat/stream` — streaming consultation (SSE)

Same pipeline as `/v1/chat` but emits `Server-Sent Events` (`text/event-stream`).

**Event sequence**

| Event | Fired | Payload |
| --- | --- | --- |
| `meta`  | exactly once, first      | `{"conversation_id": "<uuid>"}` |
| `delta` | zero or more             | `{"text": "<partial text>"}` |
| `final` | once on success          | `{conversation_id, answer, disclaimer, model, finish_reason, usage, rag_used, rag_score, sources, intent}` |
| `error` | replaces `final` on failure | `{conversation_id, code, message}` |

**SSE error codes** (`code` field inside an `error` event)

| Code | Cause |
| --- | --- |
| `openai_rate_limit` | OpenAI 429 (quota / billing). |
| `openai_auth` | OpenAI authentication failure — `OPENAI_API_KEY` issue. |
| `openai_connection` | Network error reaching OpenAI. |
| `openai_api_status` | OpenAI returned a non-2xx HTTP status. |
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

Chunk, index, and analyze a medical article from a JSON body.

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

### 2.9 `GET /metrics` — in-process metrics

Unauthenticated snapshot of request counts, token usage, RAG hit rate, error rate, plus live Redis / Qdrant sizes. **Keep behind a private network or add auth before exposing externally.**

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

Server-driven symptom intake: a fixed 10-step form that collects the chief complaint, onset, trajectory, severity (1–10), accompanying symptoms, triggers, relevant history, current medications, allergies, and an explicit red-flag screen. The server owns the step sequence; the client just forwards the user's free-text answer each turn. Output is a clinician-facing JSON report with a specialist routing recommendation from a closed enum.

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

- `in_progress` → `next_step: {step_id, question, kind, choices?, range?, clarification?}`. `clarification` is set when the server wants the user to restate (max 2 per step, then force-accepted as unparsed).
- `completed` → `report: {clinical_summary, structured, specialist_recommendation: {category, rationale}, detected_red_flags}`. `category` is one of `gp, emergency_room, urgent_care, cardiologist, neurologist, gastroenterologist, dermatologist, endocrinologist, pulmonologist, psychiatrist, gynecologist, urologist, orthopedist, otolaryngologist`; out-of-enum values fall back to `gp`.
- `red_flag_exit` → `emergency_message` (locale-specific), `detected_red_flag`, `emergency_phone`. The phone is resolved via [D.1 regionalization](#) — `region=KZ` → `"112 / 103"`, `region=US` → `"911"`, unknown region → locale-neutral default.

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
| 3 | `severity` | int_scale (1–10) | — |
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
| `409` | Session already terminated (`red_flag_exit` or `completed`) — start a new one. |
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
| `413` | File upload exceeds 10 MB. |
| `422` | Pydantic validation error (malformed JSON, wrong types). |
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
| History per request | trimmed to last 8 turns | `chat.py` |
| Rate limit | `RATE_LIMIT_PER_MINUTE` + `RATE_LIMIT_BURST` (25 default) per user per minute | `rate_limit.py` |
| Redis turn retention | `REDIS_MAX_TURNS` (12 default) | `memory.py` |
| Redis TTL | `REDIS_TTL_SECONDS` | `memory.py` |
| Article upload size | 10 MB | `articles.py` |

---

## 6. Conversation semantics

- `conversation_id` is a **UUIDv4**, ideally generated by the client. Omit it on the first request and the server will return one in the response.
- Ownership is locked on the first successful write via Redis `SET NX` — a second user sending the same `conversation_id` gets `403`.
- Redis TTL is refreshed on every append. A conversation silently expires after `REDIS_TTL_SECONDS` of inactivity.
- When history exceeds 8 turns, the older portion is summarized and replaced (the summary is cached for the conversation's lifetime).

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
