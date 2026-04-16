import time

from fastapi import HTTPException

from ..config import settings
from .redis_client import get_redis


def _key(identifier: str) -> str:
    return f"{settings.redis_prefix}:rl:{identifier}:{int(time.time() // 60)}"  # ключ на минуту


async def enforce_rate_limit(identifier: str) -> None:
    """
    Лимит на 1 минуту на идентификатор (user_id / ip / service).
    Реализация: INCR + EXPIRE на ключ минутного окна.
    """
    r = get_redis()
    key = _key(identifier)

    async with r.pipeline(transaction=True) as pipe:
        pipe.incr(key, 1)
        pipe.expire(key, 70)  # чуть больше минуты, чтобы окно точно дожило
        count, _ = await pipe.execute()

    limit = int(settings.rate_limit_per_minute) + int(settings.rate_limit_burst)
    if int(count) > limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Limit is ~{settings.rate_limit_per_minute}/min (+burst).",
        )
