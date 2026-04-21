"""Per-identifier rate limiting via Redis sliding window.

The prior floor-minute implementation (`rl:{id}:{minute}` with INCR) lets a client
empty the bucket at :59 and start over at :00, effectively doubling their allowed
rate at minute boundaries. The sliding window here scores each request by its
arrival timestamp in a sorted set, trims anything older than 60s, then counts —
any 60-second window sees at most `limit` requests regardless of boundary.
"""

import time
import uuid

from fastapi import HTTPException

from ..config import settings
from .redis_client import get_redis

_WINDOW_MS = 60_000
# Key TTL (seconds): slightly longer than the window so an idle key cleans up,
# but short enough that zombie keys don't pile up if a client disappears.
_KEY_TTL_SECONDS = 90


def _key(identifier: str) -> str:
    return f"{settings.redis_prefix}:rl:{identifier}"


async def enforce_rate_limit(identifier: str) -> None:
    """Raise 429 if `identifier` has exceeded limit+burst requests in the last 60s.

    The check-and-insert is done in a single pipelined transaction:
      1. ZREMRANGEBYSCORE — drop entries older than now - 60s
      2. ZADD             — record this request (unique member guards against
                             parallel-request collisions at the same millisecond)
      3. ZCARD            — count requests now in the window
      4. EXPIRE           — keep the key alive, let it expire on inactivity
    """
    r = get_redis()
    key = _key(identifier)
    now_ms = int(time.time() * 1000)
    limit = int(settings.rate_limit_per_minute) + int(settings.rate_limit_burst)
    member = f"{now_ms}:{uuid.uuid4().hex}"

    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now_ms - _WINDOW_MS)
        pipe.zadd(key, {member: now_ms})
        pipe.zcard(key)
        pipe.expire(key, _KEY_TTL_SECONDS)
        results = await pipe.execute()

    count = int(results[2])
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Limit is ~{settings.rate_limit_per_minute}/min (+burst).",
        )
