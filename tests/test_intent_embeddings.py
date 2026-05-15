"""Tests for the embedding-based intent fast-path (C.5)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import intent_embeddings


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Start every test with an empty exemplar cache so state doesn't leak."""
    intent_embeddings._exemplar_vectors = {}
    yield
    intent_embeddings._exemplar_vectors = {}


def _vec_for(label: str, dim: int = 4) -> list[float]:
    """Deterministic 'embedding' for a label — different labels are orthogonal-ish."""
    base = [0.0] * dim
    idx = abs(hash(label)) % dim
    base[idx] = 1.0
    return base


async def test_returns_none_before_initialization():
    result = await intent_embeddings.fast_classify_intent("what's the weather")
    assert result is None


async def test_initialize_populates_cache_for_all_categories():
    # initialize_exemplar_embeddings issues ONE batch request to
    # `client.embeddings.create` and bypasses openai_call_guard (so cold-start
    # network jitter can't trip the breaker). Mock returns one fake vector
    # per exemplar in `flat_texts` order — the dict reassembly is what we test.

    class _Item:
        def __init__(self, vec):
            self.embedding = vec

    class _Resp:
        def __init__(self, items):
            self.data = items

    total = sum(len(v) for v in intent_embeddings.INTENT_EXEMPLARS.values())
    fake_vectors = [[float(i), 0.0, 0.0, 0.0] for i in range(total)]
    fake_resp = _Resp([_Item(v) for v in fake_vectors])

    mock_create = AsyncMock(return_value=fake_resp)

    with patch("app.services.intent_embeddings.client.embeddings.create", new=mock_create):
        await intent_embeddings.initialize_exemplar_embeddings()

    assert intent_embeddings.is_initialized()
    # Exactly one batch call, not N serial calls.
    mock_create.assert_called_once()
    for category in intent_embeddings.INTENT_EXEMPLARS:
        assert category in intent_embeddings._exemplar_vectors
        assert len(intent_embeddings._exemplar_vectors[category]) == len(intent_embeddings.INTENT_EXEMPLARS[category])


async def test_initialize_failure_leaves_cache_empty_without_raising():
    # A cold-start OpenAI failure must NOT crash lifespan and must NOT trip
    # the breaker (call goes direct, no openai_call_guard). Service continues
    # with the LLM path for intent classification.
    with patch(
        "app.services.intent_embeddings.client.embeddings.create",
        new=AsyncMock(side_effect=ConnectionError("openai cold-start jitter")),
    ):
        await intent_embeddings.initialize_exemplar_embeddings()

    # Cache stays empty — fast_classify_intent returns None.
    assert intent_embeddings._exemplar_vectors == {}
    assert not intent_embeddings.is_initialized()


async def test_off_topic_message_hits_fast_path():
    """An exemplar-shaped off-topic message gets classified without an LLM call."""
    # Pin a specific off-topic exemplar and its embedding; the message embeds to
    # the same vector so cosine similarity = 1.0 > threshold.
    intent_embeddings._exemplar_vectors = {
        "off_topic": [[1.0, 0.0, 0.0, 0.0]],
        "lifestyle": [[0.0, 1.0, 0.0, 0.0]],
    }

    async def _mock_embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    with patch("app.services.intent_embeddings.embed_text", new=_mock_embed):
        result = await intent_embeddings.fast_classify_intent("what's the weather")

    assert result is not None
    category, confidence, risk_level = result
    assert category == "off_topic"
    assert confidence >= 0.82
    assert risk_level == "low"


async def test_below_threshold_falls_through_to_none():
    """When the best match isn't confident enough, the fast path abstains."""
    intent_embeddings._exemplar_vectors = {
        "off_topic": [[1.0, 0.0, 0.0, 0.0]],
    }

    # message embedding is nearly-orthogonal → cosine ~ 0
    async def _mock_embed(text: str) -> list[float]:
        return [0.0, 1.0, 0.0, 0.0]

    with patch("app.services.intent_embeddings.embed_text", new=_mock_embed):
        result = await intent_embeddings.fast_classify_intent("any text")

    assert result is None


async def test_embed_failure_returns_none():
    intent_embeddings._exemplar_vectors = {"off_topic": [[1.0, 0.0, 0.0, 0.0]]}

    with patch(
        "app.services.intent_embeddings.embed_text",
        new=AsyncMock(side_effect=ConnectionError("openai down")),
    ):
        result = await intent_embeddings.fast_classify_intent("any text")

    assert result is None


def test_emergency_not_in_fastpath_categories():
    """emergency / symptom_check / mental_health / general_health MUST NOT live in exemplars.

    Those categories need the LLM's risk calibration — a false 'closed' answer
    on an emergency is a safety incident. Guardrail: if someone adds them by
    accident, this test screams.
    """
    forbidden = {"emergency", "symptom_check", "mental_health", "general_health"}
    assert forbidden.isdisjoint(intent_embeddings.INTENT_EXEMPLARS.keys())


class TestIntentClassifyFastPathIntegration:
    """classify_intent() uses the fast path when available, records path metric."""

    async def test_fast_result_bypasses_llm(self):
        from app.services.intent import classify_intent

        async def _mock_fast(msg: str):
            return "off_topic", 0.95, "low"

        llm_spy = AsyncMock()

        with (
            patch("app.services.intent_embeddings.fast_classify_intent", new=_mock_fast),
            patch("app.services.intent.client.chat.completions.create", new=llm_spy),
        ):
            result = await classify_intent("what's the weather", history=None, redis_client=None, locale="en")

        assert result.category == "off_topic"
        assert result.confidence == 0.95
        llm_spy.assert_not_called()

    async def test_fast_miss_falls_through_to_llm(self):
        from app.services.intent import classify_intent

        async def _mock_fast(msg: str):
            return None

        # Mock the LLM response enough to parse a valid intent JSON.
        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Choice:
            class message:
                content = '{"category": "symptom_check", "confidence": 0.8, "risk_level": "medium", "requires_followup": false, "detected_entities": {}}'

        class _Resp:
            model = "gpt-4o-mini"
            usage = _Usage()
            choices = [_Choice()]

        with (
            patch("app.services.intent_embeddings.fast_classify_intent", new=_mock_fast),
            patch("app.services.intent.client.chat.completions.create", new=AsyncMock(return_value=_Resp())),
        ):
            result = await classify_intent("I have a headache", history=None, redis_client=None, locale="en")

        assert result.category == "symptom_check"
