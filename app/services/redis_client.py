import redis.asyncio as redis
from redis.asyncio import Redis
from ..config import settings

_redis: Redis | None = None

async def init_redis() -> None:
    global _redis
    _redis = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,  # чтобы получать str, а не bytes
    )
    # Проверка соединения (fail fast)
    await _redis.ping()

async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None

def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis is not initialized")
    return _redis