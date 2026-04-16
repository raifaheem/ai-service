import hashlib
import json
import logging

from ..config import settings
from .openai_client import client

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def get_embedding_dimension() -> int:
    model = settings.openai_embedding_model
    size = EMBEDDING_DIMENSIONS.get(model)
    if size is None:
        raise RuntimeError(f"Unsupported embedding model: {model}")
    return size


def normalize_text_for_embedding(text: str) -> str:
    return " ".join(text.split()).strip()


def _emb_cache_key(normalized_text: str) -> str:
    digest = hashlib.md5(normalized_text.encode(), usedforsecurity=False).hexdigest()
    return f"{settings.redis_prefix}:emb:{digest}"


async def _get_cached_embedding(redis_client, key: str) -> list[float] | None:
    try:
        raw = await redis_client.get(key)
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("Embedding cache miss or error for key %s", key)
    return None


async def _set_cached_embedding(redis_client, key: str, vector: list[float]) -> None:
    try:
        await redis_client.set(key, json.dumps(vector), ex=settings.embedding_cache_ttl)
    except Exception:
        logger.debug("Failed to cache embedding for key %s", key)


async def embed_text(text: str, redis_client=None) -> list[float]:
    normalized = normalize_text_for_embedding(text)
    if not normalized:
        raise ValueError("Text for embedding is empty")

    if redis_client:
        cache_key = _emb_cache_key(normalized)
        cached = await _get_cached_embedding(redis_client, cache_key)
        if cached is not None:
            return cached

    resp = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=normalized,
    )
    vector = resp.data[0].embedding

    if redis_client:
        await _set_cached_embedding(redis_client, cache_key, vector)

    return vector


async def embed_texts(texts: list[str], redis_client=None) -> list[list[float]]:
    normalized = [normalize_text_for_embedding(t) for t in texts]
    normalized = [t for t in normalized if t]

    if not normalized:
        return []

    results: list[list[float]] = []
    uncached_texts: list[str] = []
    uncached_indices: list[int] = []

    if redis_client:
        for i, text in enumerate(normalized):
            cache_key = _emb_cache_key(text)
            cached = await _get_cached_embedding(redis_client, cache_key)
            if cached is not None:
                results.append(cached)
            else:
                results.append([])  # placeholder
                uncached_texts.append(text)
                uncached_indices.append(i)
    else:
        uncached_texts = normalized
        uncached_indices = list(range(len(normalized)))
        results = [[] for _ in normalized]

    if uncached_texts:
        resp = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=uncached_texts,
        )
        vectors = [item.embedding for item in resp.data]

        for idx, vector in zip(uncached_indices, vectors, strict=False):
            results[idx] = vector

        if redis_client:
            for text, vector in zip(uncached_texts, vectors, strict=False):
                cache_key = _emb_cache_key(text)
                await _set_cached_embedding(redis_client, cache_key, vector)

    return results
