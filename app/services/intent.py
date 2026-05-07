import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, cast

from ..config import settings
from ..metrics import metrics
from .openai_call_guard import OpenAIUnavailable, openai_call_guard
from .openai_client import client

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "symptom_check",
    "lifestyle",
    "nutrition",
    "mental_health",
    "fitness",
    "sleep",
    "emergency",
    "general_health",
    "off_topic",
    "sensitive_blocked",
}

VALID_RISK_LEVELS = {"low", "medium", "high", "emergency"}

CATEGORY_TO_ADDON = {
    "symptom_check": "symptom_check",
    "lifestyle": "lifestyle",
    "nutrition": "lifestyle",
    "fitness": "lifestyle",
    "sleep": "lifestyle",
    "mental_health": "mental_health",
    "emergency": "emergency",
    "general_health": None,
    "off_topic": None,
    "sensitive_blocked": None,
}

CATEGORY_TO_TEMPERATURE = {
    "symptom_check": 0.3,
    "emergency": 0.2,
    "mental_health": 0.3,
    "general_health": 0.4,
    "lifestyle": 0.5,
    "nutrition": 0.5,
    "fitness": 0.5,
    "sleep": 0.5,
    "off_topic": 0.4,
    # Unused — sensitive_blocked short-circuits before LLM generation.
    "sensitive_blocked": 0.0,
}

CLASSIFY_SYSTEM_PROMPT = """You are a health query classifier. Analyze the user's message and return a JSON object with these fields:

- "category": one of: symptom_check, lifestyle, nutrition, mental_health, fitness, sleep, emergency, general_health, off_topic, sensitive_blocked
- "confidence": float 0.0-1.0
- "risk_level": one of: low, medium, high, emergency
- "requires_followup": boolean — true if the message lacks detail for a useful answer
- "detected_entities": object with optional keys: "symptoms", "body_parts", "conditions", "goals" (each an array of strings)

Classification rules:
- "emergency": chest pain, difficulty breathing, loss of consciousness, heavy bleeding, suicidal thoughts, self-harm, poisoning, severe allergic reaction
- "symptom_check": user describes physical symptoms or asks about a symptom
- "mental_health": anxiety, depression, stress, sleep disorders related to mental state, emotional issues
- "lifestyle": general wellness, habits, daily routines
- "nutrition": diet, food, vitamins, supplements
- "fitness": exercise, training, physical activity
- "sleep": sleep quality, insomnia, sleep schedule
- "general_health": general medical questions, prevention, checkups
- "off_topic": not related to health at all (e.g. weather, sports scores, programming help)
- "sensitive_blocked": user is asking about sexually-explicit content (INCLUDING clinical questions about sexual organs, STIs/STDs, sexual function), profanity, recreational drug use (non-medication), or graphic violence/gore. Self-harm and suicidal ideation are NOT sensitive_blocked — they belong in "emergency" or "mental_health".

Examples:
- "что такое ИППП?" -> sensitive_blocked
- "симптомы эректильной дисфункции" -> sensitive_blocked
- "эффекты кокаина" -> sensitive_blocked
- "у меня суицидальные мысли" -> emergency (NOT sensitive_blocked)
- "как пережить стресс" -> mental_health
- "у меня болит голова" -> symptom_check
- "как принимать ибупрофен" -> general_health (medication, not recreational drug)

Risk levels:
- "emergency": life-threatening symptoms
- "high": symptoms that need prompt medical attention (persistent severe pain, high fever, etc.)
- "medium": symptoms worth monitoring or seeing a doctor about
- "low": general wellness questions, lifestyle, off-topic, or sensitive_blocked

Return ONLY valid JSON, no markdown formatting."""


@dataclass
class IntentResult:
    category: str
    confidence: float
    requires_followup: bool
    detected_entities: dict
    risk_level: str

    @property
    def addon_name(self) -> str | None:
        return CATEGORY_TO_ADDON.get(self.category)

    @property
    def temperature(self) -> float:
        return CATEGORY_TO_TEMPERATURE.get(self.category, 0.4)


def _default_intent() -> IntentResult:
    return IntentResult(
        category="general_health",
        confidence=0.0,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )


_INTENT_CACHE_VERSION = "v3"


def _build_cache_key(message: str, history_tail: list[dict], locale: str) -> str:
    payload = json.dumps(
        {"m": message, "h": history_tail, "l": locale},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()
    return f"{settings.redis_prefix}:intent:{_INTENT_CACHE_VERSION}:{digest}"


async def _get_cached(redis_client, cache_key: str) -> IntentResult | None:
    try:
        raw = await redis_client.get(cache_key)
        if raw:
            data = json.loads(raw)
            return IntentResult(**data)
    except Exception:
        logger.debug("Intent cache miss or error for key %s", cache_key)
    return None


async def _set_cached(redis_client, cache_key: str, result: IntentResult) -> None:
    try:
        await redis_client.set(cache_key, json.dumps(asdict(result), ensure_ascii=False), ex=300)
    except Exception:
        logger.debug("Failed to cache intent for key %s", cache_key)


async def classify_intent(
    message: str,
    history: list[dict] | None = None,
    redis_client=None,
    locale: str = "ru",
) -> IntentResult:
    history_tail = (history or [])[-2:]

    if redis_client:
        cache_key = _build_cache_key(message, history_tail, locale)
        cached = await _get_cached(redis_client, cache_key)
        if cached:
            metrics.record_intent_path("cache")
            return cached

    # Embedding-based fast path (C.5) — only trusted for safe categories.
    # emergency / symptom_check / mental_health / general_health still go
    # through the LLM because getting those wrong has cost.
    from .intent_embeddings import fast_classify_intent  # local import breaks circular

    fast_result = await fast_classify_intent(message)
    if fast_result is not None:
        category, confidence, risk_level = fast_result
        result = IntentResult(
            category=category,
            confidence=confidence,
            requires_followup=False,
            detected_entities={},
            risk_level=risk_level,
        )
        if redis_client:
            await _set_cached(redis_client, cache_key, result)
        metrics.record_intent_path("fast")
        return result

    messages: list[Any] = [{"role": "system", "content": CLASSIFY_SYSTEM_PROMPT}]
    if history_tail:
        messages.extend(history_tail)
    messages.append({"role": "user", "content": message})

    try:
        _start = time.perf_counter()
        async with openai_call_guard():
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0.1,
                max_tokens=300,
                response_format=cast(Any, {"type": "json_object"}),
            )
        _duration_ms = round((time.perf_counter() - _start) * 1000, 1)
        _usage = resp.usage
        if _usage:
            logger.info(
                "OpenAI usage (intent)",
                extra={
                    "openai_model": resp.model,
                    "prompt_tokens": _usage.prompt_tokens,
                    "completion_tokens": _usage.completion_tokens,
                    "duration_ms": _duration_ms,
                    "call_type": "intent_classify",
                },
            )
            metrics.record_openai_usage(_usage.prompt_tokens, _usage.completion_tokens, call_type="intent")
        raw_json = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw_json)

        category = data.get("category", "general_health")
        if category not in VALID_CATEGORIES:
            category = "general_health"

        risk_level = data.get("risk_level", "low")
        if risk_level not in VALID_RISK_LEVELS:
            risk_level = "low"

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        result = IntentResult(
            category=category,
            confidence=confidence,
            requires_followup=bool(data.get("requires_followup", False)),
            detected_entities=data.get("detected_entities", {}),
            risk_level=risk_level,
        )

    except OpenAIUnavailable:
        logger.warning("Intent classification skipped: OpenAI breaker is open")
        result = _default_intent()
    except Exception:
        logger.exception("Intent classification failed, using default")
        result = _default_intent()

    if redis_client:
        await _set_cached(redis_client, cache_key, result)

    metrics.record_intent_path("llm")
    return result
