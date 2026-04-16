from qdrant_client import AsyncQdrantClient

from ..config import settings

_qdrant: AsyncQdrantClient | None = None


async def init_qdrant() -> None:
    global _qdrant

    _qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=settings.qdrant_timeout,
    )

    # fail fast: если Qdrant недоступен, приложение не должно тихо стартовать
    await _qdrant.get_collections()


async def close_qdrant() -> None:
    global _qdrant
    if _qdrant is not None:
        await _qdrant.close()
        _qdrant = None


def get_qdrant() -> AsyncQdrantClient:
    if _qdrant is None:
        raise RuntimeError("Qdrant is not initialized")
    return _qdrant
