import uuid

from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from ..config import settings
from .embeddings import get_embedding_dimension, embed_text, embed_texts
from .vector_client import get_qdrant


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
        return

    await client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=VectorParams(
            size=expected_size,
            distance=Distance.COSINE,
        ),
    )


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
    for chunk, vector in zip(valid_chunks, vectors):
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
