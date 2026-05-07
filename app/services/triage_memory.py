"""Redis persistence for the triage state machine (D.3.a + A6 CAS).

Mirrors the chat memory layout — prefix/key style, TTL from `settings`, owner
via SET NX. Kept in its own module so `memory.py` stays focused on chat.

Two keys per session:
- `healthai:triage:{id}:state`  — the serialized TriageSession (string, JSON).
- `healthai:triage:{id}:owner` — user_id, first-writer-wins (NX).

A6: `save_session` is now a compare-and-swap on `session.version`. Concurrent
POSTs on the same session_id used to silently overwrite each other (chat is
RPUSH so naturally serializes; triage stores a JSON blob). Now the second
writer raises `TriageConcurrentUpdate` and the router returns 409. There's
still a small window between GET and SET — the fake Redis used in tests has
no WATCH/EVAL, so this is best-effort CAS rather than fully atomic. For
triage write rates (one POST every few seconds per session) the residual
race is acceptable.
"""

from __future__ import annotations

from ..config import settings
from .redis_client import get_redis
from .triage import TriageConcurrentUpdate, TriageSession


def _state_key(session_id: str) -> str:
    return f"{settings.redis_prefix}:triage:{session_id}:state"


def _owner_key(session_id: str) -> str:
    return f"{settings.redis_prefix}:triage:{session_id}:owner"


async def save_session(session: TriageSession) -> None:
    """Persist the session + owner with compare-and-swap on `version`.

    On version mismatch raises `TriageConcurrentUpdate`; the router catches
    that and returns 409 to the client. The mutation here increments
    `session.version` so the next save by the same caller carries the new
    value — the second writer in a race always loses.
    """
    r = get_redis()
    ttl = int(settings.redis_ttl_seconds)
    state_key = _state_key(session.session_id)
    owner_key = _owner_key(session.session_id)

    # Read the current persisted version (if any).
    raw = await r.get(state_key)
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            persisted = TriageSession.from_json(raw)
        except Exception:
            # Corrupt blob — treat as missing; the new write replaces it.
            persisted = None
        if persisted is not None and persisted.version != session.version:
            raise TriageConcurrentUpdate(
                f"triage session {session.session_id} version mismatch: "
                f"expected {session.version}, found {persisted.version}"
            )

    # Bump version *before* serializing so the on-disk record carries the new value
    # and the in-memory caller's session reflects the same number.
    session.version += 1

    async with r.pipeline(transaction=True) as pipe:
        pipe.set(state_key, session.to_json(), ex=ttl)
        pipe.set(owner_key, session.user_id, nx=True, ex=ttl)
        pipe.expire(owner_key, ttl)
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
