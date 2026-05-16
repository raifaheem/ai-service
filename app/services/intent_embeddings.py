"""Embedding-based fast-path for intent classification (C.5).

classify_intent calls the LLM on every request — that's a round-trip + ~500
tokens even when the message is obviously off-topic ("what's the weather?")
or obviously an emergency ("chest pain left arm"). Cosine similarity against
a small library of per-category exemplars answers those cheap cases at
embedding cost (~10x less).

Design choices:
- Exemplars live in code (INTENT_EXEMPLARS) — deliberately small and curated.
- Only the safe categories (off_topic, lifestyle, nutrition, fitness, sleep,
  meta) are served from the fast path. `emergency` always falls through to
  the LLM — a false negative here is life-threatening, and an extra OpenAI
  call is cheap insurance.
- Embeddings are computed once at startup (`initialize_exemplar_embeddings`)
  and cached in module state.
- Threshold 0.82 is empirical; tune with the golden-set evals (C.1) once
  we have coverage numbers.
"""

from __future__ import annotations

import logging
import math

from ..config import settings
from .embeddings import embed_text, normalize_text_for_embedding
from .openai_client import client

logger = logging.getLogger(__name__)

# Only categories the LLM does not need to confirm live here. `emergency`,
# `symptom_check`, `mental_health`, `general_health` are deliberately absent —
# they require nuanced classification and risk calibration.
INTENT_EXEMPLARS: dict[str, list[str]] = {
    "off_topic": [
        "what's the weather today",
        "какая сегодня погода",
        "tell me a joke",
        "расскажи анекдот",
        "recommend a movie",
        "who won the football match",
        "write me a poem",
        "how do I code in python",
    ],
    "lifestyle": [
        "how do I build a morning routine",
        "как выработать привычку рано вставать",
        "tips for reducing screen time",
        "как больше пить воды",
    ],
    "nutrition": [
        "what foods are rich in iron",
        "какие продукты богаты железом",
        "is intermittent fasting healthy",
        "what's a balanced breakfast",
        "как правильно питаться при тренировках",
    ],
    "fitness": [
        "how often should i do cardio",
        "как часто делать кардио",
        "what muscles does squats work",
        "какую программу тренировок выбрать новичку",
    ],
    "sleep": [
        "how can i improve sleep quality",
        "как улучшить сон",
        "why do i wake up tired",
        "почему я плохо сплю",
    ],
    # Self-referential questions about the assistant itself. Greetings and
    # small-talk ("how are you?", "как у тебя дела?") deliberately stay in
    # off_topic — they're not capability questions.
    "meta": [
        "что ты умеешь",
        "чем ты можешь помочь",
        "на каком языке с тобой можно общаться",
        "на каких языках ты отвечаешь",
        "кто ты",
        "представься",
        "какие у тебя возможности",
        "о чём с тобой можно говорить",
        "what can you do",
        "what are your capabilities",
        "what languages do you speak",
        "what topics can we discuss",
        "who are you",
        "introduce yourself",
        "не істей аласың",
        "қандай тілдерде сөйлесесің",
    ],
}

# Per-category recommended risk level for fast-path results.
_FASTPATH_RISK = {
    "off_topic": "low",
    "lifestyle": "low",
    "nutrition": "low",
    "fitness": "low",
    "sleep": "low",
    "meta": "low",
}

# Empirical threshold — tune against golden-set evals.
_CONFIDENCE_THRESHOLD = 0.82

_exemplar_vectors: dict[str, list[list[float]]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def initialize_exemplar_embeddings() -> None:
    """Compute embeddings for every exemplar. Call from lifespan startup.

    Best-effort: the service still runs without the fast path (intent
    classification falls through to the LLM). To avoid blowing up the
    OpenAI circuit breaker on cold-start network jitter we:
      - issue ONE batch embedding request instead of N serial calls; and
      - call `client.embeddings.create` directly, bypassing
        `openai_call_guard`, so a startup failure can't trip the breaker
        and degrade `/v1/chat` for the next 60s.

    If the batch fails we log the traceback and leave `_exemplar_vectors`
    empty — `fast_classify_intent` returns None in that state and intent
    classification gracefully falls back to the LLM path.
    """
    global _exemplar_vectors

    # Flat list paired with the (category, index) it came from so we can
    # reassemble the dict-of-lists shape after the single batch call.
    flat_texts: list[str] = []
    origin: list[tuple[str, int]] = []
    for category, examples in INTENT_EXEMPLARS.items():
        for ex in examples:
            normalized = normalize_text_for_embedding(ex)
            if not normalized:
                continue
            flat_texts.append(normalized)
            origin.append((category, len(origin)))

    if not flat_texts:
        # Leave the cache empty so `is_initialized()` and
        # `fast_classify_intent` both correctly report the fast-path as off.
        _exemplar_vectors = {}
        return

    try:
        resp = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=flat_texts,
        )
        vectors = [item.embedding for item in resp.data]
    except Exception:
        # logger.exception includes the traceback — vital for debugging
        # cold-start failures that the old swallow-and-WARN hid.
        logger.exception(
            "intent_embeddings warmup failed — fast-path stays disabled, "
            "intent classification will use the LLM path (degraded but functional). "
            "This does NOT trip the OpenAI circuit breaker."
        )
        # Leave the cache empty so `is_initialized()` and
        # `fast_classify_intent` both correctly report the fast-path as off.
        _exemplar_vectors = {}
        return

    computed: dict[str, list[list[float]]] = {category: [] for category in INTENT_EXEMPLARS}
    for (category, _), vec in zip(origin, vectors, strict=False):
        computed[category].append(vec)
    _exemplar_vectors = computed
    logger.info(
        "Intent fast-path initialized: %d categories, %d total exemplars",
        len(computed),
        sum(len(v) for v in computed.values()),
    )


def is_initialized() -> bool:
    return bool(_exemplar_vectors)


async def fast_classify_intent(message: str) -> tuple[str, float, str] | None:
    """Return (category, confidence, risk_level) or None if nothing confident.

    Returns None when:
    - exemplar embeddings aren't initialized yet (service just started)
    - embed_text raises (OpenAI down)
    - the best category's max similarity is below _CONFIDENCE_THRESHOLD
    """
    if not _exemplar_vectors:
        return None

    try:
        message_vec = await embed_text(message)
    except Exception:
        logger.debug("fast_classify_intent: embed_text failed, falling through to LLM")
        return None

    best_category: str | None = None
    best_score = 0.0
    for category, vectors in _exemplar_vectors.items():
        if not vectors:
            continue
        score = max(_cosine(message_vec, v) for v in vectors)
        if score > best_score:
            best_score = score
            best_category = category

    if best_category and best_score >= _CONFIDENCE_THRESHOLD:
        return best_category, best_score, _FASTPATH_RISK.get(best_category, "low")
    return None
