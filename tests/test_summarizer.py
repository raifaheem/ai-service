from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.summarizer import (
    KEEP_RECENT_TURNS,
    RESUMMARIZE_AFTER_N_TURNS,
    SUMMARIZE_THRESHOLD,
    get_turns_to_summarize,
    should_summarize,
    summarize_conversation,
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


# --------------- Resummarization (A.2) ---------------


class TestResummarization:
    """Summary must refresh after RESUMMARIZE_AFTER_N_TURNS new turns.

    Exercises the chat.py::_get_or_create_summary path end-to-end with mocked
    memory and summarizer calls to prove the resummarization branch fires.
    """

    async def _run(self, history_len: int, existing_summary: str | None, meta: dict | None):
        from app.routers.chat import _get_or_create_summary

        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"} for i in range(history_len)]

        captured: dict = {}

        async def _mock_summarize(old_turns, locale):
            captured["called"] = True
            captured["num_old_turns"] = len(old_turns)
            return "fresh summary"

        async def _mock_set_summary_with_meta(conv_id, summary, turn_count):
            captured["saved_turn_count"] = turn_count
            captured["saved_summary"] = summary

        async def _mock_get_summary(conv_id):
            return existing_summary

        async def _mock_get_summary_meta(conv_id):
            return meta

        with (
            patch("app.routers.chat.summarize_conversation", side_effect=_mock_summarize),
            patch("app.routers.chat.memory.set_summary_with_meta", side_effect=_mock_set_summary_with_meta),
            patch("app.routers.chat.memory.get_summary", side_effect=_mock_get_summary),
            patch("app.routers.chat.memory.get_summary_meta", side_effect=_mock_get_summary_meta),
        ):
            summary, recent = await _get_or_create_summary("conv-1", history, locale="en")
        return summary, recent, captured

    async def test_first_summary_triggers_when_threshold_passed(self):
        summary, recent, captured = await self._run(
            history_len=SUMMARIZE_THRESHOLD + 2,
            existing_summary=None,
            meta=None,
        )
        assert summary == "fresh summary"
        assert captured.get("called") is True
        assert captured["saved_turn_count"] == SUMMARIZE_THRESHOLD + 2
        assert len(recent) == KEEP_RECENT_TURNS

    async def test_existing_fresh_summary_not_resummarized(self):
        # Summary was just taken at history_len - 1, so turns_since_last == 1 < 6 — skip.
        history_len = SUMMARIZE_THRESHOLD + 2
        summary, _, captured = await self._run(
            history_len=history_len,
            existing_summary="cached summary",
            meta={"turn_count_at_summary": history_len - 1},
        )
        assert summary == "cached summary"
        assert captured.get("called") is None  # summarizer not called

    async def test_existing_stale_summary_resummarized_after_n_turns(self):
        # Last summary at turn_count=8, now at 14. Diff = 6 == RESUMMARIZE_AFTER_N_TURNS → refresh.
        assert RESUMMARIZE_AFTER_N_TURNS == 6
        summary, _, captured = await self._run(
            history_len=14,
            existing_summary="stale summary",
            meta={"turn_count_at_summary": 8},
        )
        assert summary == "fresh summary"
        assert captured.get("called") is True
        assert captured["saved_turn_count"] == 14

    async def test_existing_summary_without_meta_resummarized(self):
        # Pre-migration case: summary exists but no meta — treat as stale.
        summary, _, captured = await self._run(
            history_len=SUMMARIZE_THRESHOLD + 4,
            existing_summary="old summary from before meta feature",
            meta=None,
        )
        assert summary == "fresh summary"
        assert captured.get("called") is True

    async def test_short_conversation_does_not_summarize(self):
        summary, recent, captured = await self._run(
            history_len=SUMMARIZE_THRESHOLD,
            existing_summary=None,
            meta=None,
        )
        assert summary is None
        assert captured.get("called") is None
        assert len(recent) == SUMMARIZE_THRESHOLD
