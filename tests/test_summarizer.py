import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.summarizer import (
    summarize_conversation,
    should_summarize,
    get_turns_to_summarize,
    SUMMARIZE_THRESHOLD,
    KEEP_RECENT_TURNS,
)


# --------------- should_summarize ---------------

def test_should_summarize_short_conversation():
    assert should_summarize(5) is False
    assert should_summarize(SUMMARIZE_THRESHOLD) is False


def test_should_summarize_long_conversation():
    assert should_summarize(SUMMARIZE_THRESHOLD + 1) is True
    assert should_summarize(20) is True


# --------------- get_turns_to_summarize ---------------

def test_get_turns_to_summarize_short():
    turns = [{"role": "user", "content": f"msg{i}"} for i in range(4)]
    old, recent = get_turns_to_summarize(turns)
    assert old == []
    assert recent == turns


def test_get_turns_to_summarize_long():
    turns = [{"role": "user", "content": f"msg{i}"} for i in range(12)]
    old, recent = get_turns_to_summarize(turns)
    assert len(recent) == KEEP_RECENT_TURNS
    assert len(old) == 12 - KEEP_RECENT_TURNS
    assert old + recent == turns


def test_get_turns_to_summarize_exact_threshold():
    turns = [{"role": "user", "content": f"msg{i}"} for i in range(SUMMARIZE_THRESHOLD)]
    old, recent = get_turns_to_summarize(turns)
    assert old == []
    assert recent == turns


# --------------- summarize_conversation ---------------

def _mock_openai_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_summarize_conversation_success():
    summary_text = "The user discussed headaches lasting 3 days."
    with patch("app.services.summarizer.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response(summary_text)
        result = await summarize_conversation(
            [{"role": "user", "content": "I have a headache"}, {"role": "assistant", "content": "How long?"}],
            locale="en",
        )

    assert result == summary_text
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_summarize_conversation_empty_turns():
    result = await summarize_conversation([], locale="ru")
    assert result is None


@pytest.mark.asyncio
async def test_summarize_conversation_api_error():
    with patch("app.services.summarizer.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("API Error")
        result = await summarize_conversation(
            [{"role": "user", "content": "test"}],
            locale="ru",
        )

    assert result is None


@pytest.mark.asyncio
async def test_summarize_conversation_uses_locale_prompt():
    with patch("app.services.summarizer.client.chat.completions.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_openai_response("summary")
        await summarize_conversation(
            [{"role": "user", "content": "test"}],
            locale="kk",
        )

    call_messages = mock_create.call_args[1]["messages"]
    # System prompt should be in Kazakh
    assert "медициналық" in call_messages[0]["content"].lower() or "контекст" in call_messages[0]["content"].lower()
