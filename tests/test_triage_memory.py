"""Redis persistence tests for D.3.a triage sessions.

Uses the shared `_FakeRedis` from conftest (via `mock_redis`) so we exercise
actual serialization paths without running real Redis.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import triage_memory
from app.services.triage import TriageSession, TriageState


@pytest.fixture
def stored_redis(mock_redis):
    """Install mock_redis as the module-level singleton for triage_memory."""
    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
    ):
        yield mock_redis


def _build_session() -> TriageSession:
    s = TriageSession.new(user_id="u-42", locale="ru", region="KZ")
    s.answers["primary_complaint"] = "headache for 3 days"
    s.answers["severity"] = 7
    s.unparsed_steps.append("onset")
    s.clarification_counts["onset"] = 2
    s.red_flags.append("Severe chest pain")
    return s


class TestSaveLoadRoundTrip:
    async def test_save_then_load_equal(self, stored_redis):
        original = _build_session()
        await triage_memory.save_session(original)
        loaded = await triage_memory.load_session(original.session_id)
        assert loaded is not None
        # Dataclass equality covers every field.
        assert loaded == original

    async def test_state_enum_round_trips(self, stored_redis):
        s = _build_session()
        s.state = TriageState.RED_FLAG_EXIT
        await triage_memory.save_session(s)
        loaded = await triage_memory.load_session(s.session_id)
        assert loaded is not None and loaded.state is TriageState.RED_FLAG_EXIT

    async def test_missing_session_returns_none(self, stored_redis):
        assert await triage_memory.load_session("does-not-exist") is None

    async def test_corrupt_payload_returns_none(self, stored_redis):
        # Stash non-JSON bytes under the expected key so load can't parse.
        s = _build_session()
        await stored_redis.set(
            f"healthai:triage:{s.session_id}:state",
            "not valid json at all",
        )
        assert await triage_memory.load_session(s.session_id) is None


class TestOwnership:
    async def test_owner_stored_on_save(self, stored_redis):
        s = _build_session()
        await triage_memory.save_session(s)
        assert await triage_memory.get_owner(s.session_id) == "u-42"

    async def test_owner_is_first_writer_wins(self, stored_redis):
        """The plan calls out the same SET NX pattern as chat conversations:
        a second writer must NOT steal ownership even if they save another
        session object under the same id (impossible in practice, but guard
        the primitive)."""
        s = _build_session()
        await triage_memory.save_session(s)
        # Simulate a separate save using a different user_id via the same id.
        attacker = TriageSession.new(user_id="evil", locale="ru", region=None)
        attacker.session_id = s.session_id
        await triage_memory.save_session(attacker)
        assert await triage_memory.get_owner(s.session_id) == "u-42"


class TestDelete:
    async def test_delete_removes_state_and_owner(self, stored_redis):
        s = _build_session()
        await triage_memory.save_session(s)
        assert await triage_memory.get_owner(s.session_id) == "u-42"
        deleted = await triage_memory.delete_session(s.session_id)
        # Exactly two keys: :state and :owner.
        assert deleted == 2
        assert await triage_memory.load_session(s.session_id) is None
        assert await triage_memory.get_owner(s.session_id) is None

    async def test_delete_is_idempotent(self, stored_redis):
        # Deleting a session that never existed returns 0 — the router turns
        # that into a 404 at the HTTP layer.
        assert await triage_memory.delete_session("ghost-session") == 0
