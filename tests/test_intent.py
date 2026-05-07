import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.intent import (
    classify_intent,
    IntentResult,
    VALID_CATEGORIES,
    VALID_RISK_LEVELS,
    CATEGORY_TO_ADDON,
    CATEGORY_TO_TEMPERATURE,
    _default_intent,
    _build_cache_key,
)


# --------------- IntentResult ---------------

def test_intent_result_addon_name():
    intent = IntentResult(
        category="symptom_check", confidence=0.9,
        requires_followup=False, detected_entities={}, risk_level="medium",
    )
    assert intent.addon_name == "symptom_check"


def test_intent_result_addon_name_none_for_general():
    intent = IntentResult(
        category="general_health", confidence=0.8,
        requires_followup=False, detected_entities={}, risk_level="low",
    )
    assert intent.addon_name is None


def test_intent_result_temperature():
    intent = IntentResult(
        category="emergency", confidence=0.95,
        requires_followup=False, detected_entities={}, risk_level="emergency",
    )
    assert intent.temperature == 0.2


def test_intent_result_temperature_lifestyle():
    intent = IntentResult(
        category="lifestyle", confidence=0.8,
        requires_followup=False, detected_entities={}, risk_level="low",
    )
    assert intent.temperature == 0.5


# --------------- Mappings completeness ---------------

def test_all_categories_have_addon_mapping():
    for cat in VALID_CATEGORIES:
        assert cat in CATEGORY_TO_ADDON


def test_all_categories_have_temperature_mapping():
    for cat in VALID_CATEGORIES:
        assert cat in CATEGORY_TO_TEMPERATURE


# --------------- _default_intent ---------------

def test_default_intent():
    d = _default_intent()
    assert d.category == "general_health"
    assert d.confidence == 0.0
    assert d.risk_level == "low"
    assert d.requires_followup is False


# --------------- _build_cache_key ---------------

def test_cache_key_deterministic():
    k1 = _build_cache_key("hello", [], "en")
    k2 = _build_cache_key("hello", [], "en")
    assert k1 == k2


def test_cache_key_differs_for_different_messages():
    k1 = _build_cache_key("hello", [], "en")
    k2 = _build_cache_key("world", [], "en")
    assert k1 != k2


def test_cache_key_differs_for_different_locales():
    k1 = _build_cache_key("hello", [], "en")
    k2 = _build_cache_key("hello", [], "ru")
    assert k1 != k2


def test_cache_key_is_versioned():
    k = _build_cache_key("hello", [], "en")
    assert ":intent:v3:" in k


# --------------- classify_intent ---------------

def _mock_openai_response(data: dict):
    choice = MagicMock()
    choice.message.content = json.dumps(data)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_classify_intent_symptom_check():
    response_data = {
        "category": "symptom_check",
        "confidence": 0.92,
        "risk_level": "medium",
        "requires_followup": True,
        "detected_entities": {"symptoms": ["headache"], "body_parts": ["head"]},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("У меня болит голова уже 3 дня")

    assert result.category == "symptom_check"
    assert result.confidence == 0.92
    assert result.risk_level == "medium"
    assert result.requires_followup is True
    assert "headache" in result.detected_entities.get("symptoms", [])


@pytest.mark.asyncio
async def test_classify_intent_emergency():
    response_data = {
        "category": "emergency",
        "confidence": 0.98,
        "risk_level": "emergency",
        "requires_followup": False,
        "detected_entities": {"symptoms": ["chest pain", "difficulty breathing"]},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("I have severe chest pain and can't breathe")

    assert result.category == "emergency"
    assert result.risk_level == "emergency"
    assert result.temperature == 0.2


@pytest.mark.asyncio
async def test_classify_intent_off_topic():
    response_data = {
        "category": "off_topic",
        "confidence": 0.85,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("What is the capital of France?")

    assert result.category == "off_topic"
    assert result.addon_name is None


@pytest.mark.asyncio
async def test_classify_intent_sensitive_blocked():
    response_data = {
        "category": "sensitive_blocked",
        "confidence": 0.9,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("опиши свои сексуальные предпочтения")

    assert result.category == "sensitive_blocked"
    # No addon for sensitive_blocked — short-circuited before LLM generation.
    assert result.addon_name is None


def test_sensitive_blocked_in_valid_categories():
    assert "sensitive_blocked" in VALID_CATEGORIES


@pytest.mark.asyncio
async def test_classify_intent_invalid_category_falls_back():
    response_data = {
        "category": "invalid_category",
        "confidence": 0.5,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("test message")

    assert result.category == "general_health"


@pytest.mark.asyncio
async def test_classify_intent_invalid_risk_level_falls_back():
    response_data = {
        "category": "symptom_check",
        "confidence": 0.7,
        "risk_level": "critical",
        "requires_followup": False,
        "detected_entities": {},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("test")

    assert result.risk_level == "low"


@pytest.mark.asyncio
async def test_classify_intent_api_error_returns_default():
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("API Error")
        result = await classify_intent("test message")

    assert result.category == "general_health"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_classify_intent_confidence_clamped():
    response_data = {
        "category": "lifestyle",
        "confidence": 1.5,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    }
    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("test")

    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_classify_intent_with_redis_cache():
    response_data = {
        "category": "nutrition",
        "confidence": 0.8,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    }

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        result = await classify_intent("What vitamins should I take?", redis_client=mock_redis)

    assert result.category == "nutrition"
    mock_redis.get.assert_called_once()
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_classify_intent_cache_hit():
    cached_data = json.dumps({
        "category": "fitness",
        "confidence": 0.9,
        "risk_level": "low",
        "requires_followup": False,
        "detected_entities": {},
    })

    mock_redis = AsyncMock()
    mock_redis.get.return_value = cached_data

    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        result = await classify_intent("How often should I exercise?", redis_client=mock_redis)

    assert result.category == "fitness"
    mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_classify_intent_uses_history_tail():
    response_data = {
        "category": "symptom_check",
        "confidence": 0.8,
        "risk_level": "medium",
        "requires_followup": False,
        "detected_entities": {},
    }
    history = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "resp1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "resp2"},
    ]

    with patch("app.services.intent.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(response_data)
        await classify_intent("follow up question", history=history)

    call_messages = mock_create.call_args[1]["messages"]
    # system + last 2 history + user = 4 messages
    assert len(call_messages) == 4
    assert call_messages[0]["role"] == "system"
    assert call_messages[-1]["content"] == "follow up question"
