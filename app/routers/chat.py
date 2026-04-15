import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from openai import RateLimitError, APIConnectionError, AuthenticationError, APIStatusError

from ..schemas import ChatRequest, ChatResponse, ChatSource, ChatIntent
from ..security import auth_guard, resolve_user_id
from ..services.llm import generate_health_answer, stream_health_answer
from ..services import memory
from ..services.rate_limit import enforce_rate_limit
from ..services.rag import build_rag_context, compress_sources
from ..services.i18n import normalize_locale, get_disclaimer, get_prompt_addon
from ..services.intent import classify_intent, IntentResult
from ..services.summarizer import summarize_conversation, should_summarize, get_turns_to_summarize
from ..services.safety import detect_injection, sanitize_input, INJECTION_REFUSAL
from ..services.content_filter import check_response_safety
from ..services.circuit_breaker import openai_breaker, DEGRADED_MESSAGES

router = APIRouter(prefix="/v1", tags=["chat"])

OFF_TOPIC_MESSAGES = {
    "ru": "Я — ассистент по здоровью и могу помочь с вопросами о здоровье, питании, физической активности, сне и ментальном благополучии. Пожалуйста, задайте вопрос, связанный со здоровьем.",
    "en": "I'm a health assistant and can help with questions about health, nutrition, physical activity, sleep, and mental well-being. Please ask a health-related question.",
    "kk": "Мен денсаулық көмекшісімін және денсаулық, тамақтану, дене белсенділігі, ұйқы және ментальді әл-ауқат туралы сұрақтарға көмектесе аламын. Денсаулыққа қатысты сұрақ қойыңыз.",
}


def _resolve_addon_prompt(intent: IntentResult, locale: str) -> str | None:
    addon_name = intent.addon_name
    if not addon_name:
        return None
    addon = get_prompt_addon(addon_name, locale)
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


async def _get_or_create_summary(
    conversation_id: str,
    history: list[dict],
    locale: str,
) -> tuple[str | None, list[dict]]:
    """Get existing summary or create one if conversation is long enough.

    Returns (summary, trimmed_history) where trimmed_history contains
    only the recent turns to send to the LLM.
    """
    if not should_summarize(len(history)):
        return None, history

    # Check for existing summary
    existing_summary = None
    try:
        existing_summary = await memory.get_summary(conversation_id)
    except Exception:
        pass

    old_turns, recent_turns = get_turns_to_summarize(history)

    if not existing_summary and old_turns:
        try:
            new_summary = await summarize_conversation(old_turns, locale=locale)
            if new_summary:
                await memory.set_summary(conversation_id, new_summary)
                existing_summary = new_summary
        except Exception:
            logging.exception("Failed to summarize conversation %s", conversation_id)

    return existing_summary, recent_turns


_PROFILE_LABELS = {
    "ru": {
        "age": "Возраст", "sex": "Пол", "conditions": "Хронические/особенности",
        "goals": "Цели", "allergies": "Аллергии", "medications": "Принимаемые препараты",
        "height": "Рост (см)", "weight": "Вес (кг)", "activity": "Уровень активности",
        "bmi": "ИМТ",
    },
    "en": {
        "age": "Age", "sex": "Sex", "conditions": "Chronic conditions",
        "goals": "Goals", "allergies": "Allergies", "medications": "Current medications",
        "height": "Height (cm)", "weight": "Weight (kg)", "activity": "Activity level",
        "bmi": "BMI",
    },
    "kk": {
        "age": "Жасы", "sex": "Жынысы", "conditions": "Созылмалы аурулар",
        "goals": "Мақсаттар", "allergies": "Аллергиялар", "medications": "Қабылдайтын дәрілер",
        "height": "Бойы (см)", "weight": "Салмағы (кг)", "activity": "Белсенділік деңгейі",
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    conversation_id = req.conversation_id or str(uuid.uuid4())
    user_id = resolve_user_id(auth, x_user_id)
    locale = normalize_locale(req.locale)
    prof = profile_to_text(req, locale=locale)
    disclaimer = get_disclaimer(locale)

    rate_limit_id = f"user:{user_id}"
    await enforce_rate_limit(rate_limit_id)

    # Safety: detect prompt injection
    if detect_injection(req.message):
        refusal = INJECTION_REFUSAL.get(locale, INJECTION_REFUSAL["ru"])
        return ChatResponse(
            answer=refusal,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
        )

    # Safety: sanitize input
    user_message = sanitize_input(req.message)

    if req.conversation_id:
        owner = await memory.get_owner(conversation_id)
        if owner and owner != user_id:
            raise HTTPException(status_code=403, detail="Access denied to this conversation")

    history = request_history_to_messages(req)
    if not history:
        turns = await memory.get_history(conversation_id)
        history = [{"role": t.role, "content": t.content} for t in turns]

    redis_client = _get_redis_or_none()
    intent = await classify_intent(user_message, history=history, redis_client=redis_client)

    if intent.category == "off_topic" and intent.confidence >= 0.7:
        off_topic_answer = OFF_TOPIC_MESSAGES.get(locale, OFF_TOPIC_MESSAGES["ru"])
        return ChatResponse(
            answer=off_topic_answer,
            disclaimer=disclaimer,
            conversation_id=conversation_id,
            rag_used=False,
            intent=ChatIntent(category=intent.category, risk_level=intent.risk_level, confidence=intent.confidence),
        )

    summary, trimmed_history = await _get_or_create_summary(conversation_id, history, locale)
    addon_prompt = _resolve_addon_prompt(intent, locale)

    # RAG with fallback
    rag_context, rag_chunks, rag_score = "", [], None
    try:
        rag_context, rag_chunks, rag_score = await build_rag_context(
            query=user_message,
            limit=5,
            language=locale,
            redis_client=redis_client,
        )
    except Exception:
        logging.warning("RAG unavailable for conversation %s, proceeding without context", conversation_id)
    sources = compress_sources(rag_chunks)

    # Circuit breaker check
    if not openai_breaker.is_available:
        degraded = DEGRADED_MESSAGES.get(locale, DEGRADED_MESSAGES["ru"])
        raise HTTPException(status_code=503, detail=degraded)

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
        openai_breaker.record_success()
    except (RateLimitError, APIConnectionError, AuthenticationError, APIStatusError) as e:
        openai_breaker.record_failure()
        status_code, message, _ = map_openai_error(e)
        raise HTTPException(status_code, message)

    # Content safety filter
    raw_answer, applied_filters = check_response_safety(answer, locale=locale)
    if disclaimer.lower() not in raw_answer.lower():
        answer_to_user = f"{raw_answer}\n\n{disclaimer}"
    else:
        answer_to_user = raw_answer

    try:
        await memory.append_turns(
            conversation_id,
            [
                memory.make_turn("user", user_message),
                memory.make_turn("assistant", raw_answer),
            ],
            user_id=user_id,
        )
        await memory.update_metadata(
            conversation_id,
            topic=intent.category,
            turn_count=len(history) + 2,
        )
    except Exception:
        logging.exception("Failed to persist conversation %s to Redis", conversation_id)

    return ChatResponse(
        answer=answer_to_user,
        disclaimer=disclaimer,
        conversation_id=conversation_id,
        rag_used=bool(rag_chunks),
        rag_score=rag_score,
        sources=[ChatSource(**item) for item in sources] or None,
        intent=ChatIntent(category=intent.category, risk_level=intent.risk_level, confidence=intent.confidence),
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    conversation_id = req.conversation_id or str(uuid.uuid4())
    user_id = resolve_user_id(auth, x_user_id)
    locale = normalize_locale(req.locale)
    prof = profile_to_text(req, locale=locale)
    disclaimer = get_disclaimer(locale)

    rate_limit_id = f"user:{user_id}"
    await enforce_rate_limit(rate_limit_id)

    # Safety: detect prompt injection
    if detect_injection(req.message):
        refusal = INJECTION_REFUSAL.get(locale, INJECTION_REFUSAL["ru"])

        async def injection_generator():
            yield _sse("meta", {"conversation_id": conversation_id})
            yield _sse("delta", {"text": refusal})
            yield _sse("final", {
                "conversation_id": conversation_id,
                "answer": refusal,
                "disclaimer": disclaimer,
                "model": None,
                "finish_reason": "injection_blocked",
                "usage": None,
                "rag_used": False,
                "sources": [],
            })

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(injection_generator(), media_type="text/event-stream", headers=headers)

    # Safety: sanitize input
    user_message = sanitize_input(req.message)

    if req.conversation_id:
        owner = await memory.get_owner(conversation_id)
        if owner and owner != user_id:
            raise HTTPException(status_code=403, detail="Access denied to this conversation")

    history = request_history_to_messages(req)
    if not history:
        turns = await memory.get_history(conversation_id)
        history = [{"role": t.role, "content": t.content} for t in turns]

    redis_client = _get_redis_or_none()
    intent = await classify_intent(user_message, history=history, redis_client=redis_client)

    if intent.category == "off_topic" and intent.confidence >= 0.7:
        off_topic_answer = OFF_TOPIC_MESSAGES.get(locale, OFF_TOPIC_MESSAGES["ru"])

        async def off_topic_generator():
            yield _sse("meta", {"conversation_id": conversation_id})
            yield _sse("delta", {"text": off_topic_answer})
            yield _sse("final", {
                "conversation_id": conversation_id,
                "answer": off_topic_answer,
                "disclaimer": disclaimer,
                "model": None,
                "finish_reason": "off_topic",
                "usage": None,
                "rag_used": False,
                "sources": [],
                "intent": {"category": intent.category, "risk_level": intent.risk_level, "confidence": intent.confidence},
            })

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(off_topic_generator(), media_type="text/event-stream", headers=headers)

    summary, trimmed_history = await _get_or_create_summary(conversation_id, history, locale)
    addon_prompt = _resolve_addon_prompt(intent, locale)

    # RAG with fallback
    rag_context, rag_chunks, rag_score = "", [], None
    try:
        rag_context, rag_chunks, rag_score = await build_rag_context(
            query=user_message,
            limit=5,
            language=locale,
            redis_client=redis_client,
        )
    except Exception:
        logging.warning("RAG unavailable for stream %s, proceeding without context", conversation_id)
    sources = compress_sources(rag_chunks)

    # Circuit breaker check
    if not openai_breaker.is_available:
        degraded = DEGRADED_MESSAGES.get(locale, DEGRADED_MESSAGES["ru"])

        async def degraded_generator():
            yield _sse("meta", {"conversation_id": conversation_id})
            yield _sse("error", {
                "conversation_id": conversation_id,
                "code": "service_degraded",
                "message": degraded,
            })

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(degraded_generator(), media_type="text/event-stream", headers=headers)

    async def event_generator():
        yield _sse("meta", {"conversation_id": conversation_id})

        parts: list[str] = []
        usage_payload: dict | None = None
        model_name: str | None = None
        finish_reason: str | None = None

        try:
            async for ev in stream_health_answer(
                user_message,
                locale=locale,
                profile_text=prof,
                history=trimmed_history,
                rag_context=rag_context,
                addon_prompt=addon_prompt,
                temperature=intent.temperature,
                summary=summary,
            ):
                if ev.get("type") == "delta":
                    text = ev.get("text", "")
                    if text:
                        parts.append(text)
                        yield _sse("delta", {"text": text})

                elif ev.get("type") == "usage":
                    usage_payload = ev.get("usage")
                    model_name = ev.get("model")
                    finish_reason = ev.get("finish_reason")

            openai_breaker.record_success()
            raw_answer = "".join(parts).strip()
            # Content safety filter
            raw_answer, _filters = check_response_safety(raw_answer, locale=locale)
            answer_to_user = raw_answer
            if disclaimer.lower() not in answer_to_user.lower():
                answer_to_user = f"{answer_to_user}\n\n{disclaimer}"

            try:
                await memory.append_turns(
                    conversation_id,
                    [
                        memory.make_turn("user", user_message),
                        memory.make_turn("assistant", raw_answer),
                    ],
                    user_id=user_id,
                )
                await memory.update_metadata(
                    conversation_id,
                    topic=intent.category,
                    turn_count=len(history) + 2,
                )
            except Exception:
                logging.exception("Failed to persist conversation %s to Redis", conversation_id)

            yield _sse(
                "final",
                {
                    "conversation_id": conversation_id,
                    "answer": answer_to_user,
                    "disclaimer": disclaimer,
                    "model": model_name,
                    "finish_reason": finish_reason,
                    "usage": usage_payload,
                    "rag_used": bool(rag_chunks),
                    "rag_score": rag_score,
                    "sources": sources,
                    "intent": {"category": intent.category, "risk_level": intent.risk_level, "confidence": intent.confidence},
                },
            )

        except (RateLimitError, APIConnectionError, AuthenticationError, APIStatusError) as e:
            openai_breaker.record_failure()
            _, message, code = map_openai_error(e)
            yield _sse(
                "error",
                {
                    "conversation_id": conversation_id,
                    "code": code,
                    "message": message,
                },
            )
        except Exception as e:
            logging.exception("Unexpected error in chat stream for %s", conversation_id)
            yield _sse(
                "error",
                {
                    "conversation_id": conversation_id,
                    "code": "internal_error",
                    "message": "Internal server error.",
                },
            )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def request_history_to_messages(req: ChatRequest) -> list[dict]:
    if not req.history:
        return []

    result = []
    for t in req.history[-8:]:
        content = t.content.strip()
        if content:
            result.append({"role": t.role, "content": content})
    return result



def map_openai_error(e: Exception) -> tuple[int, str, str]:
    if isinstance(e, RateLimitError):
        return 503, "OpenAI quota/billing issue.", "openai_rate_limit"
    if isinstance(e, AuthenticationError):
        return 502, "OpenAI auth failed. Check OPENAI_API_KEY.", "openai_auth"
    if isinstance(e, APIConnectionError):
        return 502, "OpenAI connection error.", "openai_connection"
    if isinstance(e, APIStatusError):
        return 502, f"OpenAI API error: {e.status_code}", "openai_api_status"
    return 500, "Internal server error.", "internal_error"