import uuid

import httpx
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from ..config import settings
from .breaker_guard import breaker_guard
from .circuit_breaker import qdrant_breaker
from .embeddings import embed_text, embed_texts, get_embedding_dimension
from .vector_client import get_qdrant


class QdrantUnavailable(Exception):
    """Raised when the Qdrant breaker is open or the call is in flight when one trips."""


# Exceptions that should count as breaker failures. ConnectionError and TimeoutError
# cover socket-level issues; UnexpectedResponse / ResponseHandlingException cover
# qdrant-client surfacing 5xx and HTTP transport problems.
_QDRANT_RECORDED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    UnexpectedResponse,
    ResponseHandlingException,
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
)


def _qdrant_guard():
    return breaker_guard(qdrant_breaker, QdrantUnavailable, _QDRANT_RECORDED_EXCEPTIONS)


def _extract_collection_vector_size(collection_info) -> int | None:
    config = getattr(collection_info, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", None)
    return getattr(vectors, "size", None)


async def ensure_qdrant_collection() -> None:
    client = get_qdrant()
    expected_size = get_embedding_dimension()

    collections = await client.get_collections()
    existing_names = {c.name for c in collections.collections}

    if settings.qdrant_collection in existing_names:
        info = await client.get_collection(settings.qdrant_collection)
        existing_size = _extract_collection_vector_size(info)

        if existing_size is not None and existing_size != expected_size:
            raise RuntimeError(
                f"Qdrant collection '{settings.qdrant_collection}' has vector size {existing_size}, "
                f"but embedding model '{settings.openai_embedding_model}' requires {expected_size}. "
                "Use another collection name or recreate the collection."
            )
    else:
        await client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(
                size=expected_size,
                distance=Distance.COSINE,
            ),
        )

    # Ensure payload indexes on the two fields we filter by. Qdrant Cloud
    # refuses filter operations on un-indexed payload keys with HTTP 400
    # ("Index required but not found"); legacy Qdrant <1.12 silently scanned.
    # `create_payload_index` is idempotent — it no-ops if the index already
    # exists with the same schema.
    for field in ("source_id", "language"):
        try:
            await client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except UnexpectedResponse as e:
            # 409 = already exists with a different schema (not our case);
            # log and continue so collection setup doesn't block startup.
            if getattr(e, "status_code", None) not in (200, 201, 409):
                raise


async def upsert_text_chunks(chunks: list[dict], redis_client=None) -> int:
    if not chunks:
        return 0

    client = get_qdrant()

    valid_chunks = [c for c in chunks if " ".join(c["text"].split()).strip()]
    if not valid_chunks:
        return 0

    texts = [chunk["text"] for chunk in valid_chunks]
    vectors = await embed_texts(texts, redis_client=redis_client)

    points: list[PointStruct] = []
    for chunk, vector in zip(valid_chunks, vectors, strict=False):
        payload = {
            "text": chunk["text"],
            "source_id": chunk["source_id"],
            "title": chunk.get("title"),
            "language": chunk.get("language", "ru"),
            "metadata": chunk.get("metadata", {}),
        }

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload=payload,
            )
        )

    async with _qdrant_guard():
        await client.upsert(
            collection_name=settings.qdrant_collection,
            points=points,
        )

    return len(points)


def _qdrant_results_to_items(results) -> list[dict]:
    items: list[dict] = []
    for item in results:
        payload = item.payload or {}
        items.append(
            {
                "id": str(item.id),
                "score": float(item.score),
                "text": payload.get("text", ""),
                "source_id": payload.get("source_id", ""),
                "title": payload.get("title"),
                "language": payload.get("language"),
                "metadata": payload.get("metadata", {}),
            }
        )
    return items


async def collect_corpus_stats(scroll_batch: int = 256) -> dict:
    """Scroll the whole collection and aggregate chunk + source counts per language.

    Intended for the dev `/v1/rag/stats` endpoint. O(n) in corpus size — fine at
    phase-12 scale (hundreds of chunks); swap to per-language `count()` calls if
    the corpus ever grows past a few thousand points.
    """
    client = get_qdrant()

    by_language: dict[str, int] = {}
    sources_by_language: dict[str, set[str]] = {}
    all_sources: set[str] = set()
    total = 0

    offset = None
    while True:
        points, next_offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            limit=scroll_batch,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            lang = payload.get("language") or "unknown"
            source_id = payload.get("source_id") or ""

            by_language[lang] = by_language.get(lang, 0) + 1
            if source_id:
                sources_by_language.setdefault(lang, set()).add(source_id)
                all_sources.add(source_id)
            total += 1

        if not next_offset:
            break
        offset = next_offset

    return {
        "collection": settings.qdrant_collection,
        "total_chunks": total,
        "total_sources": len(all_sources),
        "by_language": by_language,
        "sources_by_language": {lang: len(ids) for lang, ids in sources_by_language.items()},
    }


async def delete_chunks_by_source(source_id: str) -> int:
    """Remove every point with `payload.source_id == source_id`. Returns the number of points deleted.

    Qdrant's delete-by-filter doesn't report a count directly, so we count matching
    points via `count()` before issuing the delete.
    """
    client = get_qdrant()

    filt = Filter(must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))])

    count_result = await client.count(
        collection_name=settings.qdrant_collection,
        count_filter=filt,
        exact=True,
    )
    matched = int(getattr(count_result, "count", 0) or 0)

    if matched == 0:
        return 0

    async with _qdrant_guard():
        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=FilterSelector(filter=filt),
        )
    return matched


async def search_text_chunks(
    query: str,
    limit: int = 5,
    language: str | None = None,
    redis_client=None,
    fallback_languages: list[str] | None = None,
) -> list[dict]:
    client = get_qdrant()
    query_vector = await embed_text(query, redis_client=redis_client)

    query_filter = None
    if language:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="language",
                    match=MatchValue(value=language),
                )
            ]
        )

    async with _qdrant_guard():
        results = await client.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )

    items = _qdrant_results_to_items(results)

    # Apply score threshold
    threshold = settings.rag_score_threshold
    items = [item for item in items if item["score"] >= threshold]

    # Multilingual fallback: if too few results and fallback_languages provided
    if len(items) < 2 and fallback_languages and language:
        async with _qdrant_guard():
            fallback_results = await client.search(
                collection_name=settings.qdrant_collection,
                query_vector=query_vector,
                limit=limit,
                query_filter=None,
                with_payload=True,
            )
        fallback_items = _qdrant_results_to_items(fallback_results)
        fallback_items = [item for item in fallback_items if item["score"] >= threshold]

        existing_ids = {item["id"] for item in items}
        for fb_item in fallback_items:
            if fb_item["id"] not in existing_ids:
                fb_item["is_fallback"] = True
                items.append(fb_item)
                existing_ids.add(fb_item["id"])
            if len(items) >= limit:
                break

    return items
