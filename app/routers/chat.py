import contextlib
import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

from ..context import get_request_id, set_conversation_id, set_user_id
from ..metrics import metrics
from ..schemas import ChatIntent, ChatRequest, ChatResponse, ChatSource
from ..security import auth_guard, resolve_user_id
from ..services import memory
from ..services.audit import (
    EVENT_CHAT_ANSWER,
    EVENT_CHAT_INJECTION_BLOCKED,
    EVENT_CHAT_SENSITIVE_BLOCKED,
    record_audit_event,
)
from ..services.chat_stream import StreamContext, chat_event_generator, sse
from ..services.circuit_breaker import DEGRADED_MESSAGES, openai_breaker
from ..services.content_filter import check_response_safety
from ..services.content_safety import SENSITIVE_REFUSAL, detect_sensitive_topic
from ..services.i18n import get_disclaimer, get_emergency_phone, get_prompt_addon, normalize_locale
from ..services.intent import IntentResult, classify_intent
from ..services.llm import generate_health_answer
from ..services.openai_call_guard import OpenAIUnavailable
from ..services.rag import build_rag_context, compress_sources
from ..services.rate_limit import enforce_rate_limit
from ..services.safety import INJECTION_REFUSAL, detect_injection, sanitize_input
from ..services.summarizer import (
    RESUMMARIZE_AFTER_N_TURNS,
    get_turns_to_summarize,
    should_summarize,
    summarize_conversation,
)
from ..tracing import tracer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])

OFF_TOPIC_MESSAGES = {
    "ru": "Я — ассистент по здоровью и могу помочь с вопросами о здоровье, питании, физической активности, сне и ментальном благополучии. Пожалуйста, задайте вопрос, связанный со здоровьем.",
    "en": "I'm a health assistant and can help with questions about health, nutrition, physical activity, sleep, and mental well-being. Please ask a health-related question.",
    "kk": "Мен денсаулық көмекшісімін және денсаулық, тамақтану, дене белсенділігі, ұйқы және ментальді әл-ауқат туралы сұрақтарға көмектесе аламын. Денсаулыққа қатысты сұрақ қойыңыз.",
}


def _idempotency_fingerprint(req: ChatRequest) -> str:
    """Stable hash of the request body fields that should affect the response.

    Reusing an idempotency key with *different* fields (message, conversation,
    region) is a client bug — A5 surfaces it as 409 rather than silently
    replaying the prior cached answer.
    """
    parts = [
        (req.message or "").encode("utf-8"),
        (req.conversation_id or "").encode("utf-8"),
        (req.locale or "").encode("utf-8"),
        (req.region or "").encode("utf-8"),
    ]
    return hashlib.sha256(b"\x1f".join(parts), usedforsecurity=False).hexdigest()


def _resolve_addon_prompt(intent: IntentResult, locale: str, region: str | None = None) -> str | None:
    addon_name = intent.addon_name
    if not addon_name:
        return None
    addon = get_prompt_addon(addon_name, locale)
    if addon and addon_name == "emergency":
        # Inject the region-aware phone number into the template placeholder.
        # The template carries {emergency_phone} verbatim; format it exactly once
        # here so the LLM sees a concrete number (or a neutral phrase fallback).
        addon = addon.format(emergency_phone=get_emergency_phone(region, locale))
    if addon and intent.requires_followup:
        followup_hint = {
            "ru": "\nВАЖНО: Информации недостаточно. Задай пользователю уточняющие вопросы прежде чем давать рекомендации.",
            "en": "\nIMPORTANT: Information is insufficient. Ask the user clarifying questions before giving recommendations.",
            "kk": "\nМАҢЫЗДЫ: Ақпарат жеткіліксіз. Ұсыныстар бермес бұрын пайдаланушыға нақтылау сұрақтарын қой.",
        }
        addon = addon + followup_hint.get(locale, followup_hint["ru"])
    return addon


def _get_redis_or_none():
    try:
        from ..services.redis_client import get_redis

        return get_redis()
    except Exception:
        return None


async def _persist_turns(
    conversation_id: str,
    user_message: str,
    assistant_message: str,
    user_id: str,
    topic: str,
    turn_count: int | None = None,
) -> None:
    try:
        await memory.append_turns(
            conversation_id,
            [
                memory.make_turn("user", user_message),
                memory.make_turn("assistant", assistant_message),
            ],
            user_id=user_id,
        )
        meta_update: dict[str, Any] = {"topic": topic}
        if turn_count is not None:
            meta_update["turn_count"] = turn_count
        await memory.update_metadata(conversation_id, **meta_update)
    except Exception:
        logger.exception("Failed to persist conversation %s to Redis", conversation_id)


async def _get_or_create_summary(
    conversation_id: str,
    history: list[dict],
    locale: str,
) -> tuple[str | None, list[dict]]:
    """Get existing summary or (re)create one if enough new turns accumulated.

    Returns (summary, trimmed_history) where trimmed_history contains
    only the recent turns to send to the LLM.

    Resummarizes when either there's no summary yet, or when
    RESUMMARIZE_AFTER_N_TURNS turns have been added since the last summary.
    An existing summary without meta (pre-migration) is treated as stale,
    so the first qualifying chat after deploy refreshes it.
    """
    if not should_summarize(len(history)):
        return None, history

    existing_summary: str | None = None
    summary_meta: dict | None = None
    with contextlib.suppress(Exception):
        existing_summary = await memory.get_summary(conversation_id)
        summary_meta = await memory.get_summary_meta(conversation_id)

    if summary_meta:
        turns_since_last = len(history) - int(summary_meta.get("turn_count_at_summary", 0))
    else:
        # No meta recorded — either no prior summary, or an old one from before this
        # feature landed. Treat all current history as "since last summary" so the
        # pipeline refreshes on the next qualifying request.
        turns_since_last = len(history)

    needs_resummarize = (not existing_summary) or (turns_since_last >= RESUMMARIZE_AFTER_N_TURNS)

    old_turns, recent_turns = get_turns_to_summarize(history)

    if needs_resummarize and old_turns:
        try:
            new_summary = await summarize_conversation(old_turns, locale=locale)
            if new_summary:
                await memory.set_summary_with_meta(conversation_id, new_summary, len(history))
                existing_summary = new_summary
        except Exception:
            logger.exception("Failed to resummarize conversation %s", conversation_id)

    return existing_summary, recent_turns


_PROFILE_LABELS = {
    "ru": {
        "age": "Возраст",
        "sex": "Пол",
        "conditions": "Хронические/особенности",
        "goals": "Цели",
        "allergies": "Аллергии",
        "medications": "Принимаемые препараты",
        "height": "Рост (см)",
        "weight": "Вес (кг)",
        "activity": "Уровень активности",
        "bmi": "ИМТ",
    },
    "en": {
        "age": "Age",
        "sex": "Sex",
        "conditions": "Chronic conditions",
        "goals": "Goals",
        "allergies": "Allergies",
        "medications": "Current medications",
        "height": "Height (cm)",
        "weight": "Weight (kg)",
        "activity": "Activity level",
        "bmi": "BMI",
    },
    "kk": {
        "age": "Жасы",
        "sex": "Жынысы",
        "conditions": "Созылмалы аурулар",
        "goals": "Мақсаттар",
        "allergies": "Аллергиялар",
        "medications": "Қабылдайтын дәрілер",
        "height": "Бойы (см)",
        "weight": "Салмағы (кг)",
        "activity": "Белсенділік деңгейі",
        "bmi": "ДСИ",
    },
}


def profile_to_text(req: ChatRequest, locale: str = "ru") -> str | None:
    if not req.profile:
        return None
    p = req.profile
    labels = _PROFILE_LABELS.get(locale, _PROFILE_LABELS["ru"])
    parts = []
    if p.age is not None:
        parts.append(f"{labels['age']}: {p.age}")
    if p.sex:
        parts.append(f"{labels['sex']}: {p.sex}")
    if p.height_cm is not None:
        parts.append(f"{labels['height']}: {p.height_cm}")
    if p.weight_kg is not None:
        parts.append(f"{labels['weight']}: {p.weight_kg}")
    if p.height_cm and p.weight_kg:
        bmi = round(p.weight_kg / ((p.height_cm / 100) ** 2), 1)
        parts.append(f"{labels['bmi']}: {bmi}")
    if p.activity_level:
        parts.append(f"{labels['activity']}: {p.activity_level}")
    if p.conditions:
        parts.append(f"{labels['conditions']}: " + ", ".join(p.conditions))
    if p.allergies:
        parts.append(f"{labels['allergies']}: " + ", ".join(p.allergies))
    if p.medications:
        parts.append(f"{labels['medications']}: " + ", ".join(p.medications))
    if p.goals:
        parts.append(f"{labels['goals']}: " + ", ".join(p.goals))
    return "; ".join(parts) if parts else None


_CHAT_DESCRIPTION = """\
Main medical-consultation endpoint. Runs the full pipeline:

1. **Auth** — JWT (RS256) or `X-Service-Token` + `X-User-Id`.
2. **Rate limit** — per-user, per-minute window.
3. **Prompt-injection guard** — refusal returned verbatim when detected.
4. **Ownership check** — if `conversation_id` is supplied, the caller must be its owner.
5. **Intent classification** — off-topic messages short-circuit with a canned reply.
6. **Summarization** — long histories are summarized before being sent to the LLM.
7. **RAG** — retrieves and filters medical-corpus chunks; fails open if Qdrant is down.
8. **LLM call** — cognitive prompt + locale addon + RAG + trimmed history.
9. **Content safety filter** — softens definitive diagnoses, appends disclaimer if missing.
10. **Persist** — appends the user/assistant turns to Redis.

Returns the assistant's answer with disclaimer, intent, and RAG sources.
"""


_CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Input validation failed (e.g., message too long, metadata over 5 KB)."},
    401: {"description": "Missing or invalid authentication (JWT or `X-Service-Token`)."},
    403: {"description": "`conversation_id` belongs to a different user."},
    429: {"description": "Rate limit exceeded (`RATE_LIMIT_PER_MINUTE` + `RATE_LIMIT_BURST`)."},
    502: {"description": "Upstream OpenAI error (auth, connection, or API status)."},
    503: {"description": "Service degraded — OpenAI circuit breaker open or quota exceeded."},
}


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a message to the AI assistant (sync)",
    description=_CHAT_DESCRIPTION,
    responses=_CHAT_RESPONSES,
)
async def chat(
    req: ChatRequest,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    conversation_id = req.conversation_id or str(uuid.uuid4())
    user_id = resolve_user_id(auth, x_user_id)
    set_conversation_id(conversation_id)
    set_user_id(user_id)
    locale = normalize_locale(req.locale)
    prof = profile_to_text(req, locale=locale)
    disclaimer = get_disclaimer(locale)

    # Idempotency (C.4 + A5): short-circuit retries that carry the same key AND
    # the same body. A different body under the same key is a real client bug
    # we want surfaced as 409 — silently returning a stale answer would mask it.
    fingerprint = _idempotency_fingerprint(req)
    if req.idempotency_key:
        try:
            entry = await memory.get_idempotent_entry(user_id, req.idempotency_key)
        except Exception:
            entry = None
        if entry:
            if entry.get("fingerprint") != fingerprint:
                logger.warning(
                    "Idempotency conflict for user=%s key=%s: same key, different body",
                    user_id,
                    req.idempotency_key,
                )
                raise HTTPException(
                    status_code=409,
                    detail="idempotency_key reused with a different request body",
                )
            logger.info(
                "Idempotency hit for user=%s key=%s; returning cached response",
                user_id,
                req.idempotency_key,
            )
            # M1: audit the replay so forensics doesn't lose retried requests.
            # We reuse `chat.answer` with `cached=true` rather than a new event
            # type — keeps "answers served" a single dashboard query.
            cached_response = ChatResponse(**entry["response"])
            cached_intent = cached_response.intent
            await record_audit_event(
                EVENT_CHAT_ANSWER,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=get_request_id(),
                intent_category=(cached_intent.category if cached_intent else ""),
                risk_level=(cached_intent.risk_level if cached_intent else ""),
                rag_used=bool(cached_response.rag_used),
                rag_score=cached_response.rag_score,
                locale=locale,
                cached=True,
                idempotency_key=req.idempotency_key,
            )
            return cached_response

    rate_limit_id = f"user:{user_id}"
    await enforce_rate_limit(rate_limit_id)

    if req.conversation_id:
        # B1: fail-closed when Redis can't confirm ownership. The pre-blocker
        # behaviour was to log + proceed, which let an attacker who could
        # transiently disrupt Redis read/inject into another user's
        # conversation. We now refuse with 503 — the request will be retried
        # once Redis is healthy again, the matching `triage` and
        # `conversations` routers already work this way (errors propagate).
        try:
            owner = await memory.get_owner(conversation_id)
        except Exception as e:
            logger.warning("Owner lookup failed for %s; refusing request", conversation_id)
            raise HTTPException(
                status_code=503,
                detail="Conversation ownership lookup unavailable; retry shortly.",
            ) from e
        if owner and owner != user_id:
            raise HTTPException(status_code=403, detail="Access denied to this conversation")

    # Safety: detect prompt injection (checked on raw input, then sanitized copy is persisted)
    if detect_injection(req.message):
        sanitized = sanitize_input(req.message)
        refusal = INJECTION_REFUSAL.get(locale, INJECTION_REFUSAL["ru"])
        await _persist_turns(conversation_id, sanitized, refusal, user_id, topic="blocked_injection")
        await record_audit_event(
            EVENT_CHAT_INJECTION_BLOCKED,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            locale=locale,
        )
        return ChatResponse(
            answer=refusal,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
        )

    # Sensitive-topic policy block: cheap regex pre-LLM gate for off-policy
    # content (sexual / profanity / recreational drugs / violence). Self-harm
    # and suicidal ideation are handled by the mental_health intent addon and
    # are deliberately NOT included here.
    sensitive = detect_sensitive_topic(req.message)
    if sensitive is not None:
        sanitized = sanitize_input(req.message)
        refusal = SENSITIVE_REFUSAL.get(locale, SENSITIVE_REFUSAL["ru"])
        await _persist_turns(conversation_id, sanitized, refusal, user_id, topic="blocked_sensitive")
        await record_audit_event(
            EVENT_CHAT_SENSITIVE_BLOCKED,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            locale=locale,
            sensitive_category=sensitive.category,
            locale_hit=sensitive.locale_hit,
            pattern_id=sensitive.pattern_id,
        )
        return ChatResponse(
            answer=refusal,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
        )

    # Safety: sanitize input
    user_message = sanitize_input(req.message)

    history = request_history_to_messages(req)
    if not history:
        turns = await memory.get_history(conversation_id)
        history = [{"role": t.role, "content": t.content} for t in turns]

    redis_client = _get_redis_or_none()
    with tracer.start_as_current_span("intent.classify") as span:
        intent = await classify_intent(user_message, history=history, redis_client=redis_client, locale=locale)
        span.set_attribute("intent.category", intent.category)
        span.set_attribute("intent.confidence", float(intent.confidence))
        span.set_attribute("intent.risk_level", intent.risk_level)
    metrics.record_intent(intent.category, risk_level=intent.risk_level)

    # LLM-fallback for content the regex pre-gate missed (paraphrase, novel
    # obfuscation, foreign-language slang). The regex is the cheap first line;
    # the classifier is the catch-net.
    if intent.category == "sensitive_blocked":
        refusal = SENSITIVE_REFUSAL.get(locale, SENSITIVE_REFUSAL["ru"])
        await _persist_turns(
            conversation_id,
            user_message,
            refusal,
            user_id,
            topic="blocked_sensitive_llm",
            turn_count=len(history) + 2,
        )
        await record_audit_event(
            EVENT_CHAT_SENSITIVE_BLOCKED,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            locale=locale,
            sensitive_category="llm_classified",
            locale_hit="n/a",
            pattern_id="intent_llm",
        )
        return ChatResponse(
            answer=refusal,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
            intent=ChatIntent(category=intent.category, risk_level=intent.risk_level, confidence=intent.confidence),
        )

    if intent.category == "off_topic" and intent.confidence >= 0.7:
        off_topic_answer = OFF_TOPIC_MESSAGES.get(locale, OFF_TOPIC_MESSAGES["ru"])
        await _persist_turns(
            conversation_id,
            user_message,
            off_topic_answer,
            user_id,
            topic="off_topic",
            turn_count=len(history) + 2,
        )
        return ChatResponse(
            answer=off_topic_answer,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
            intent=ChatIntent(category=intent.category, risk_level=intent.risk_level, confidence=intent.confidence),
        )

    summary, trimmed_history = await _get_or_create_summary(conversation_id, history, locale)
    addon_prompt = _resolve_addon_prompt(intent, locale, req.region)

    # RAG with fallback
    rag_context: str = ""
    rag_chunks: list[dict] = []
    rag_score: float | None = None
    with tracer.start_as_current_span("rag.build") as span:
        span.set_attribute("rag.locale", locale)
        try:
            rag_context, rag_chunks, rag_score = await build_rag_context(
                query=user_message,
                limit=5,
                language=locale,
                redis_client=redis_client,
            )
        except Exception:
            logger.warning("RAG unavailable for conversation %s, proceeding without context", conversation_id)
        span.set_attribute("rag.chunks", len(rag_chunks))
        if rag_score is not None:
            span.set_attribute("rag.top_score", float(rag_score))
    sources = compress_sources(rag_chunks)
    metrics.record_rag_result(bool(rag_chunks))

    # Circuit breaker check
    if not await openai_breaker.is_available:
        degraded = DEGRADED_MESSAGES.get(locale, DEGRADED_MESSAGES["ru"])
        raise HTTPException(status_code=503, detail=degraded)

    with tracer.start_as_current_span("llm.generate") as span:
        span.set_attribute("llm.locale", locale)
        span.set_attribute("llm.temperature", float(intent.temperature))
        try:
            answer = await generate_health_answer(
                user_message,
                locale=locale,
                profile_text=prof,
                history=trimmed_history,
                rag_context=rag_context,
                addon_prompt=addon_prompt,
                temperature=intent.temperature,
                summary=summary,
            )
        except OpenAIUnavailable as e:
            degraded = DEGRADED_MESSAGES.get(locale, DEGRADED_MESSAGES["ru"])
            raise HTTPException(status_code=503, detail=degraded) from e
        except (RateLimitError, APIConnectionError, AuthenticationError, APIStatusError) as e:
            # Breaker already recorded the failure inside the guard.
            status_code, message, _ = map_openai_error(e)
            raise HTTPException(status_code, message) from e

    # Content safety filter
    raw_answer, applied_filters = check_response_safety(answer, locale=locale)
    if disclaimer.lower() not in raw_answer.lower():
        answer_to_user = f"{raw_answer}\n\n{disclaimer}"
    else:
        answer_to_user = raw_answer

    await _persist_turns(
        conversation_id,
        user_message,
        raw_answer,
        user_id,
        topic=intent.category,
        turn_count=len(history) + 2,
    )

    await record_audit_event(
        EVENT_CHAT_ANSWER,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=get_request_id(),
        intent_category=intent.category,
        risk_level=intent.risk_level,
        rag_used=bool(rag_chunks),
        rag_score=rag_score,
        locale=locale,
        applied_filters=list(applied_filters),
    )

    response = ChatResponse(
        answer=answer_to_user,
        disclaimer=disclaimer,
        conversation_id=conversation_id,
        rag_used=bool(rag_chunks),
        rag_score=rag_score,
        sources=[ChatSource(**item) for item in sources] or None,
        intent=ChatIntent(category=intent.category, risk_level=intent.risk_level, confidence=intent.confidence),
    )

    # Cache for idempotent retries (10-minute TTL). Non-fatal on Redis errors.
    if req.idempotency_key:
        try:
            await memory.set_idempotent_entry(
                user_id,
                req.idempotency_key,
                fingerprint,
                response.model_dump(mode="json"),
            )
        except Exception:
            logger.debug("Failed to cache idempotent response for %s", req.idempotency_key)

    return response


_CHAT_STREAM_DESCRIPTION = """\
Same pipeline as `POST /v1/chat`, delivered as **Server-Sent Events** (`text/event-stream`).

**Event sequence:**
- `meta` — emitted first, payload: `{"conversation_id": "<uuid>"}`.
- `delta` — zero or more, payload: `{"text": "<partial text>"}`.
- `final` — emitted once on success. Payload includes `answer`, `disclaimer`,
  `conversation_id`, `model`, `finish_reason`, `usage`, `rag_used`, `rag_score`,
  `sources`, and `intent`.
- `error` — may replace `final` on failure. Payload: `{conversation_id, code, message}`
  where `code` is one of `openai_rate_limit`, `openai_auth`, `openai_connection`,
  `openai_api_status`, `service_degraded`, `internal_error`.

Response headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
"""


@router.post(
    "/chat/stream",
    summary="Send a message to the AI assistant (SSE streaming)",
    description=_CHAT_STREAM_DESCRIPTION,
    responses={
        200: {
            "description": "Server-Sent Events stream.",
            "content": {
                "text/event-stream": {
                    "example": (
                        'event: meta\ndata: {"conversation_id": "c3a1b2d4-5678-4abc-9def-0123456789ab"}\n\n'
                        'event: delta\ndata: {"text": "Головная "}\n\n'
                        'event: delta\ndata: {"text": "боль..."}\n\n'
                        'event: final\ndata: {"answer": "...", "disclaimer": "..."}\n\n'
                    )
                }
            },
        },
        401: {"description": "Missing or invalid authentication."},
        403: {"description": "`conversation_id` belongs to a different user."},
        429: {"description": "Rate limit exceeded."},
    },
)
async def chat_stream(
    req: ChatRequest,
    request: Request,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    conversation_id = req.conversation_id or str(uuid.uuid4())
    user_id = resolve_user_id(auth, x_user_id)
    set_conversation_id(conversation_id)
    set_user_id(user_id)
    locale = normalize_locale(req.locale)
    prof = profile_to_text(req, locale=locale)
    disclaimer = get_disclaimer(locale)

    rate_limit_id = f"user:{user_id}"
    await enforce_rate_limit(rate_limit_id)

    if req.conversation_id:
        # B1: fail-closed when Redis can't confirm ownership. The pre-blocker
        # behaviour was to log + proceed, which let an attacker who could
        # transiently disrupt Redis read/inject into another user's
        # conversation. We now refuse with 503 — the request will be retried
        # once Redis is healthy again, the matching `triage` and
        # `conversations` routers already work this way (errors propagate).
        try:
            owner = await memory.get_owner(conversation_id)
        except Exception as e:
            logger.warning("Owner lookup failed for %s; refusing request", conversation_id)
            raise HTTPException(
                status_code=503,
                detail="Conversation ownership lookup unavailable; retry shortly.",
            ) from e
        if owner and owner != user_id:
            raise HTTPException(status_code=403, detail="Access denied to this conversation")

    # Safety: detect prompt injection
    if detect_injection(req.message):
        sanitized = sanitize_input(req.message)
        refusal = INJECTION_REFUSAL.get(locale, INJECTION_REFUSAL["ru"])
        await _persist_turns(conversation_id, sanitized, refusal, user_id, topic="blocked_injection")

        async def injection_generator():
            yield sse("meta", {"conversation_id": conversation_id})
            yield sse("delta", {"text": refusal})
            yield sse(
                "final",
                {
                    "conversation_id": conversation_id,
                    "answer": refusal,
                    "disclaimer": disclaimer,
                    "model": None,
                    "finish_reason": "injection_blocked",
                    "usage": None,
                    "rag_used": False,
                    "sources": [],
                },
            )

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(injection_generator(), media_type="text/event-stream", headers=headers)

    # Sensitive-topic policy block (mirror of sync chat path)
    sensitive = detect_sensitive_topic(req.message)
    if sensitive is not None:
        sanitized = sanitize_input(req.message)
        refusal = SENSITIVE_REFUSAL.get(locale, SENSITIVE_REFUSAL["ru"])
        await _persist_turns(conversation_id, sanitized, refusal, user_id, topic="blocked_sensitive")
        await record_audit_event(
            EVENT_CHAT_SENSITIVE_BLOCKED,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            locale=locale,
            sensitive_category=sensitive.category,
            locale_hit=sensitive.locale_hit,
            pattern_id=sensitive.pattern_id,
        )

        async def sensitive_generator():
            yield sse("meta", {"conversation_id": conversation_id})
            yield sse("delta", {"text": refusal})
            yield sse(
                "final",
                {
                    "conversation_id": conversation_id,
                    "answer": refusal,
                    "disclaimer": disclaimer,
                    "model": None,
                    "finish_reason": "sensitive_blocked",
                    "usage": None,
                    "rag_used": False,
                    "sources": [],
                },
            )

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(sensitive_generator(), media_type="text/event-stream", headers=headers)

    # Safety: sanitize input
    user_message = sanitize_input(req.message)

    history = request_history_to_messages(req)
    if not history:
        turns = await memory.get_history(conversation_id)
        history = [{"role": t.role, "content": t.content} for t in turns]

    redis_client = _get_redis_or_none()
    intent = await classify_intent(user_message, history=history, redis_client=redis_client, locale=locale)
    metrics.record_intent(intent.category, risk_level=intent.risk_level)

    # LLM-fallback for sensitive content the regex pre-gate missed (SSE).
    if intent.category == "sensitive_blocked":
        refusal = SENSITIVE_REFUSAL.get(locale, SENSITIVE_REFUSAL["ru"])
        await _persist_turns(
            conversation_id,
            user_message,
            refusal,
            user_id,
            topic="blocked_sensitive_llm",
            turn_count=len(history) + 2,
        )
        await record_audit_event(
            EVENT_CHAT_SENSITIVE_BLOCKED,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            locale=locale,
            sensitive_category="llm_classified",
            locale_hit="n/a",
            pattern_id="intent_llm",
        )

        async def sensitive_llm_generator():
            yield sse("meta", {"conversation_id": conversation_id})
            yield sse("delta", {"text": refusal})
            yield sse(
                "final",
                {
                    "conversation_id": conversation_id,
                    "answer": refusal,
                    "disclaimer": disclaimer,
                    "model": None,
                    "finish_reason": "sensitive_blocked",
                    "usage": None,
                    "rag_used": False,
                    "sources": [],
                    "intent": {
                        "category": intent.category,
                        "risk_level": intent.risk_level,
                        "confidence": intent.confidence,
                    },
                },
            )

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(sensitive_llm_generator(), media_type="text/event-stream", headers=headers)

    if intent.category == "off_topic" and intent.confidence >= 0.7:
        off_topic_answer = OFF_TOPIC_MESSAGES.get(locale, OFF_TOPIC_MESSAGES["ru"])
        await _persist_turns(
            conversation_id,
            user_message,
            off_topic_answer,
            user_id,
            topic="off_topic",
            turn_count=len(history) + 2,
        )

        async def off_topic_generator():
            yield sse("meta", {"conversation_id": conversation_id})
            yield sse("delta", {"text": off_topic_answer})
            yield sse(
                "final",
                {
                    "conversation_id": conversation_id,
                    "answer": off_topic_answer,
                    "disclaimer": disclaimer,
                    "model": None,
                    "finish_reason": "off_topic",
                    "usage": None,
                    "rag_used": False,
                    "sources": [],
                    "intent": {
                        "category": intent.category,
                        "risk_level": intent.risk_level,
                        "confidence": intent.confidence,
                    },
                },
            )

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(off_topic_generator(), media_type="text/event-stream", headers=headers)

    summary, trimmed_history = await _get_or_create_summary(conversation_id, history, locale)
    addon_prompt = _resolve_addon_prompt(intent, locale, req.region)

    # RAG with fallback
    rag_context: str = ""
    rag_chunks: list[dict] = []
    rag_score: float | None = None
    try:
        rag_context, rag_chunks, rag_score = await build_rag_context(
            query=user_message,
            limit=5,
            language=locale,
            redis_client=redis_client,
        )
    except Exception:
        logger.warning("RAG unavailable for stream %s, proceeding without context", conversation_id)
    sources = compress_sources(rag_chunks)
    metrics.record_rag_result(bool(rag_chunks))

    # Circuit breaker check
    if not await openai_breaker.is_available:
        degraded = DEGRADED_MESSAGES.get(locale, DEGRADED_MESSAGES["ru"])

        async def degraded_generator():
            yield sse("meta", {"conversation_id": conversation_id})
            yield sse(
                "error",
                {
                    "conversation_id": conversation_id,
                    "code": "service_degraded",
                    "message": degraded,
                },
            )

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(degraded_generator(), media_type="text/event-stream", headers=headers)

    ctx = StreamContext(
        conversation_id=conversation_id,
        user_id=user_id,
        locale=locale,
        user_message=user_message,
        profile_text=prof,
        history_count=len(history),
        trimmed_history=trimmed_history,
        summary=summary,
        rag_context=rag_context,
        rag_chunks=rag_chunks,
        rag_score=rag_score,
        sources=sources,
        addon_prompt=addon_prompt,
        intent=intent,
        disclaimer=disclaimer,
    )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        chat_event_generator(ctx, request),
        media_type="text/event-stream",
        headers=headers,
    )


def request_history_to_messages(req: ChatRequest) -> list[dict]:
    """Convert client-supplied history turns into LLM-message dicts.

    M11: previously sliced to the last 8 turns, which diverged from the Redis
    path (`memory.get_history` returns up to REDIS_MAX_TURNS=12 unchanged).
    Now the whole client-supplied history flows through; the summarizer
    (`SUMMARIZE_THRESHOLD=8`) decides what to keep verbatim vs replace with a
    summary, so both paths converge.
    """
    if not req.history:
        return []

    result = []
    for t in req.history:
        content = t.content.strip()
        if content:
            result.append({"role": t.role, "content": content})
    return result


# User-facing message is intentionally generic — the upstream provider, error
# class, status code, and config-sensitive hints are all an information-disclosure
# risk if echoed verbatim to clients. The discriminator `code` (machine-readable)
# stays specific so clients/SDKs can branch on it; logs keep the original
# exception for operators.
_GENERIC_UPSTREAM_MESSAGE = "Upstream service unavailable."


def map_openai_error(e: Exception) -> tuple[int, str, str]:
    if isinstance(e, RateLimitError):
        return 503, _GENERIC_UPSTREAM_MESSAGE, "openai_rate_limit"
    if isinstance(e, AuthenticationError):
        return 502, _GENERIC_UPSTREAM_MESSAGE, "openai_auth"
    if isinstance(e, APIConnectionError):
        return 502, _GENERIC_UPSTREAM_MESSAGE, "openai_connection"
    if isinstance(e, APIStatusError):
        return 502, _GENERIC_UPSTREAM_MESSAGE, "openai_api_status"
    return 500, "Internal server error.", "internal_error"
