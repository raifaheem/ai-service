from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Literal

from ..config import settings
from .redis_client import get_redis

Role = Literal["user", "assistant"]


@dataclass
class Turn:
    role: Role
    content: str
    ts: float


def _key(conversation_id: str) -> str:
    return f"{settings.redis_prefix}:conv:{conversation_id}:turns"


def _owner_key(conversation_id: str) -> str:
    return f"{settings.redis_prefix}:conv:{conversation_id}:owner"


def _summary_key(conversation_id: str) -> str:
    return f"{settings.redis_prefix}:conv:{conversation_id}:summary"


def _meta_key(conversation_id: str) -> str:
    return f"{settings.redis_prefix}:conv:{conversation_id}:meta"


async def get_history(conversation_id: str) -> list[Turn]:
    r = get_redis()
    key = _key(conversation_id)
    items = await r.lrange(key, 0, -1)  # старое -> новое
    turns: list[Turn] = []
    for s in items:
        try:
            obj = json.loads(s)
            turns.append(Turn(role=obj["role"], content=obj["content"], ts=float(obj["ts"])))
        except Exception:
            logger.warning("Skipping corrupt turn in conversation %s: %s", conversation_id, s[:200])
            continue
    return turns


async def append_turns(
    conversation_id: str,
    turns: list[Turn],
    user_id: str | None = None,
) -> None:
    if not turns:
        return

    r = get_redis()
    key = _key(conversation_id)
    o_key = _owner_key(conversation_id)

    payloads = [json.dumps({"role": t.role, "content": t.content, "ts": t.ts}, ensure_ascii=False) for t in turns]
    max_turns = int(settings.redis_max_turns)
    ttl = int(settings.redis_ttl_seconds)

    async with r.pipeline(transaction=True) as pipe:
        pipe.rpush(key, *payloads)
        pipe.ltrim(key, -max_turns, -1)
        pipe.expire(key, ttl)
        if user_id:
            pipe.set(o_key, user_id, nx=True, ex=ttl)
        pipe.expire(o_key, ttl)
        await pipe.execute()


def make_turn(role: Role, content: str) -> Turn:
    return Turn(role=role, content=content, ts=time.time())


async def get_owner(conversation_id: str) -> str | None:
    r = get_redis()
    val = await r.get(_owner_key(conversation_id))
    if isinstance(val, bytes):
        return val.decode()
    return val


async def get_ttl(conversation_id: str) -> int:
    r = get_redis()
    key = _key(conversation_id)
    return await r.ttl(key)  # -1 нет TTL, -2 ключа нет, иначе секунды


# --------------- Summary ---------------


async def get_summary(conversation_id: str) -> str | None:
    r = get_redis()
    return await r.get(_summary_key(conversation_id))


async def set_summary(conversation_id: str, summary: str) -> None:
    r = get_redis()
    ttl = int(settings.redis_ttl_seconds)
    await r.set(_summary_key(conversation_id), summary, ex=ttl)


# --------------- Conversation Metadata ---------------


async def get_metadata(conversation_id: str) -> dict | None:
    r = get_redis()
    raw = await r.get(_meta_key(conversation_id))
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


async def set_metadata(conversation_id: str, metadata: dict) -> None:
    r = get_redis()
    ttl = int(settings.redis_ttl_seconds)
    await r.set(_meta_key(conversation_id), json.dumps(metadata, ensure_ascii=False), ex=ttl)


async def update_metadata(conversation_id: str, **kwargs) -> dict:
    """Update specific fields in conversation metadata, creating if needed."""
    meta = await get_metadata(conversation_id) or {}
    meta.update(kwargs)
    await set_metadata(conversation_id, meta)
    return meta


async def delete_conversation(conversation_id: str) -> int:
    r = get_redis()
    return await r.delete(
        _key(conversation_id),
        _owner_key(conversation_id),
        _summary_key(conversation_id),
        _meta_key(conversation_id),
    )
