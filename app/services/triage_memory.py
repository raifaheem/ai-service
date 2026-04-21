"""Redis persistence for the triage state machine (D.3.a).

Mirrors the chat memory layout — prefix/key style, TTL from `settings`, owner
via SET NX. Kept in its own module so `memory.py` stays focused on chat.

Two keys per session:
- `healthai:triage:{id}:state`  — the serialized TriageSession (string, JSON).
- `healthai:triage:{id}:owner` — user_id, first-writer-wins (NX).
"""

from __future__ import annotations

from ..config import settings
from .redis_client import get_redis
from .triage import TriageSession


def _state_key(session_id: str) -> str:
    return f"{settings.redis_prefix}:triage:{session_id}:state"


def _owner_key(session_id: str) -> str:
    return f"{settings.redis_prefix}:triage:{session_id}:owner"


async def save_session(session: TriageSession) -> None:
    """Persist the session + owner in one transaction.

    Uses SET NX on owner so parallel creates can't steal ownership, and
    plain SET on state so updates land. TTL refreshes both on every write —
    an active session stays alive; an abandoned one expires naturally.
    """
    r = get_redis()
    ttl = int(settings.redis_ttl_seconds)
    async with r.pipeline(transaction=True) as pipe:
        pipe.set(_state_key(session.session_id), session.to_json(), ex=ttl)
        pipe.set(_owner_key(session.session_id), session.user_id, nx=True, ex=ttl)
        pipe.expire(_owner_key(session.session_id), ttl)
        await pipe.execute()


async def load_session(session_id: str) -> TriageSession | None:
    r = get_redis()
    raw = await r.get(_state_key(session_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return TriageSession.from_json(raw)
    except Exception:
        # Corrupt state is effectively a missing session — the alternative is
        # letting one bad record break recovery for every subsequent request.
        return None


async def get_owner(session_id: str) -> str | None:
    r = get_redis()
    val = await r.get(_owner_key(session_id))
    if isinstance(val, bytes):
        return val.decode()
    return val


async def delete_session(session_id: str) -> int:
    r = get_redis()
    return await r.delete(_state_key(session_id), _owner_key(session_id))


async def get_ttl(session_id: str) -> int:
    """Remaining TTL for the state key. -2 if missing, -1 if no expiry."""
    r = get_redis()
    return await r.ttl(_state_key(session_id))
