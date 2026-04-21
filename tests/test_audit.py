"""Tests for audit log service (C.3).

Audit events land in a Redis stream keyed `{prefix}:audit`. Entries must carry
identifiers + metadata only — user message content / LLM answers are banned.
"""

from unittest.mock import AsyncMock, patch

from app.services.audit import (
    EVENT_CHAT_ANSWER,
    EVENT_CHAT_INJECTION_BLOCKED,
    EVENT_CONVERSATION_DELETE,
    _stream_key,
    record_audit_event,
)


async def test_record_audit_event_writes_to_redis_stream():
    mock_redis = AsyncMock()
    with patch("app.services.audit.get_redis", return_value=mock_redis):
        await record_audit_event(
            EVENT_CHAT_ANSWER,
            user_id="user-1",
            conversation_id="conv-1",
            request_id="req-1",
            intent_category="symptom_check",
            risk_level="medium",
            rag_used=True,
        )

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    assert call_args[0][0] == _stream_key()
    entry = call_args[0][1]
    assert entry["type"] == EVENT_CHAT_ANSWER
    assert entry["user_id"] == "user-1"
    assert entry["conversation_id"] == "conv-1"
    assert entry["request_id"] == "req-1"
    assert entry["intent_category"] == "symptom_check"
    assert entry["risk_level"] == "medium"
    assert entry["rag_used"] == "True"
    assert call_args[1]["maxlen"] == 1_000_000
    assert call_args[1]["approximate"] is True


async def test_entries_do_not_carry_message_content():
    """Guard: the caller can't accidentally pipe user_message/answer content through kwargs."""
    mock_redis = AsyncMock()
    with patch("app.services.audit.get_redis", return_value=mock_redis):
        await record_audit_event(
            EVENT_CHAT_ANSWER,
            user_id="user-1",
            conversation_id="conv-1",
            request_id="req-1",
            intent_category="symptom_check",
        )

    entry = mock_redis.xadd.call_args[0][1]
    # The entry must not contain keys that would tempt future callers to leak payloads.
    forbidden = {"user_message", "answer", "raw_answer", "message", "profile_text", "turn_content"}
    assert forbidden.isdisjoint(entry.keys())


async def test_record_audit_event_serializes_dicts_and_lists():
    mock_redis = AsyncMock()
    with patch("app.services.audit.get_redis", return_value=mock_redis):
        await record_audit_event(
            EVENT_CHAT_ANSWER,
            user_id="user-1",
            conversation_id="conv-1",
            request_id="req-1",
            applied_filters=["dosage_warning"],
            tokens={"prompt": 10, "completion": 20},
        )

    entry = mock_redis.xadd.call_args[0][1]
    # Lists and dicts are JSON-encoded so they can round-trip out of Redis.
    assert entry["applied_filters"] == '["dosage_warning"]'
    assert '"prompt": 10' in entry["tokens"]


async def test_record_audit_event_swallows_redis_errors():
    """Audit failures must not propagate — the main request flow is what matters."""
    failing = AsyncMock()
    failing.xadd = AsyncMock(side_effect=ConnectionError("redis down"))

    with patch("app.services.audit.get_redis", return_value=failing):
        # No exception escapes.
        await record_audit_event(EVENT_CONVERSATION_DELETE, user_id="user-1")


async def test_known_event_types_are_strings():
    """Sanity: the exported event constants are non-empty strings."""
    assert isinstance(EVENT_CHAT_ANSWER, str) and EVENT_CHAT_ANSWER
    assert isinstance(EVENT_CHAT_INJECTION_BLOCKED, str) and EVENT_CHAT_INJECTION_BLOCKED
    assert isinstance(EVENT_CONVERSATION_DELETE, str) and EVENT_CONVERSATION_DELETE
