"""Diagnose why RAG retrieval is returning 0 chunks against Qdrant Cloud.

Reads QDRANT_URL / QDRANT_API_KEY / OPENAI_API_KEY from environment, runs a
real search for a sample Russian query, and prints:
  1. Total point count in the collection
  2. Distribution of `language` payload values
  3. Top-10 nearest chunks (with scores) — both filtered to ru and unfiltered

Run from .venv:
    python scripts/debug_rag.py "что такое мигрень?"
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue


COLLECTION = os.environ.get("QDRANT_COLLECTION", "medical_articles")
EMBED_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


async def main(query: str) -> None:
    qdrant = AsyncQdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        prefer_grpc=False,
        timeout=10,
    )
    openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print(f"\n=== Collection: {COLLECTION} ===")
    info = await qdrant.get_collection(COLLECTION)
    print(f"points_count: {info.points_count}")
    print(f"vectors size: {info.config.params.vectors.size}")

    print(f"\n=== Sampling payload.language ===")
    pts, _ = await qdrant.scroll(
        collection_name=COLLECTION,
        limit=100,
        with_payload=True,
        with_vectors=False,
    )
    langs = Counter(p.payload.get("language", "<missing>") for p in pts)
    for lang, n in langs.most_common():
        print(f"  language={lang!r}: {n}")

    print(f"\n=== Embedding query: {query!r} ===")
    resp = await openai.embeddings.create(model=EMBED_MODEL, input=query)
    qvec = resp.data[0].embedding

    print(f"\n=== Top-10 nearest (NO filter) ===")
    hits = await qdrant.search(
        collection_name=COLLECTION,
        query_vector=qvec,
        limit=10,
        with_payload=True,
    )
    for h in hits:
        title = (h.payload or {}).get("title", "(no title)")
        lang = (h.payload or {}).get("language", "?")
        src = (h.payload or {}).get("source_id", "?")
        print(f"  score={h.score:.3f} lang={lang} src={src} title={title!r}")

    print(f"\n=== Top-10 nearest (filter language=ru) ===")
    hits_ru = await qdrant.search(
        collection_name=COLLECTION,
        query_vector=qvec,
        limit=10,
        with_payload=True,
        query_filter=Filter(
            must=[FieldCondition(key="language", match=MatchValue(value="ru"))]
        ),
    )
    for h in hits_ru:
        title = (h.payload or {}).get("title", "(no title)")
        src = (h.payload or {}).get("source_id", "?")
        print(f"  score={h.score:.3f} src={src} title={title!r}")

    print(f"\n=== Verdict ===")
    threshold = float(os.environ.get("RAG_SCORE_THRESHOLD", "0.35"))
    if not hits_ru:
        print(f"NO RU chunks — check language payload (above) vs filter")
    elif hits_ru[0].score < threshold:
        print(
            f"Top score {hits_ru[0].score:.3f} < threshold {threshold} → "
            f"RAG returns empty. Lower RAG_SCORE_THRESHOLD or rephrase query."
        )
    else:
        print(f"RAG should be returning {sum(1 for h in hits_ru if h.score >= threshold)} chunks (≥{threshold}).")

    await qdrant.close()


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "что такое мигрень?"
    asyncio.run(main(query))
