"""Seed the RAG knowledge base directly into Qdrant + Redis — no HTTP hop.

Unlike scripts/seed_knowledge_base.py (which drives /v1/articles/analyze over
HTTP and therefore needs `ENABLE_DEV_ROUTES=true` on the target service), this
script reuses the same client helpers the app uses internally:
- init_redis / init_qdrant for the singletons
- ensure_qdrant_collection to refuse size-mismatched collections
- chunk_article_with_headers for header-aware chunking
- upsert_text_chunks for vector insertion

Production-safe: runs with `ENABLE_DEV_ROUTES=false`. Intended to run on a
host that has network reach to Qdrant and Redis (e.g. via the docker-compose
`--profile seed` one-shot service).

Example:
    python scripts/seed_direct.py --manifest data/knowledge_base/manifest.json
    docker compose --profile seed run --rm seed   # prod overlay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.article_parser import chunk_article_with_headers  # noqa: E402
from app.services.redis_client import close_redis, init_redis  # noqa: E402
from app.services.vector_client import close_qdrant, init_qdrant  # noqa: E402
from app.services.vector_store import (  # noqa: E402
    delete_chunks_by_source,
    ensure_qdrant_collection,
    upsert_text_chunks,
)

logger = logging.getLogger(__name__)


async def seed_one(entry: dict, base_path: Path, overwrite: bool) -> dict:
    """Load one manifest entry, chunk with headers, upsert into Qdrant.

    Returns a per-entry summary dict: {source_id, chunks, deleted}.
    """
    article_path = base_path / entry["file"]
    text = article_path.read_text(encoding="utf-8")

    deleted = 0
    if overwrite:
        deleted = await delete_chunks_by_source(entry["source_id"])

    chunk_dicts = chunk_article_with_headers(text)
    vector_chunks = [
        {
            "text": c["text"],
            "source_id": entry["source_id"],
            "title": entry["title"],
            "language": entry["language"],
            "metadata": {
                "type": "medical_article",
                "topic": entry.get("topic", ""),
                "header": c.get("header"),
                "attribution": entry.get("attribution", {}),
            },
        }
        for c in chunk_dicts
    ]

    indexed = await upsert_text_chunks(vector_chunks)
    return {"source_id": entry["source_id"], "chunks": indexed, "deleted": deleted}


async def run(manifest_path: Path, overwrite: bool) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_path = manifest_path.parent
    entries = manifest.get("articles", [])
    if not entries:
        logger.error("Manifest has no `articles` entries: %s", manifest_path)
        return 2

    await init_redis()
    await init_qdrant()
    try:
        await ensure_qdrant_collection()

        total_chunks = 0
        failed: list[tuple[str, str]] = []
        for entry in entries:
            source_id = entry.get("source_id", "<unknown>")
            try:
                result = await seed_one(entry, base_path, overwrite)
                total_chunks += result["chunks"]
                logger.info(
                    "seeded %s: %d chunks (deleted %d existing)",
                    source_id,
                    result["chunks"],
                    result["deleted"],
                )
            except Exception as exc:
                logger.exception("failed to seed %s", source_id)
                failed.append((source_id, str(exc)))

        logger.info(
            "seed complete: %d entries, %d total chunks, %d failed",
            len(entries),
            total_chunks,
            len(failed),
        )
        return 1 if failed else 0
    finally:
        await close_qdrant()
        await close_redis()


def main() -> int:
    parser = argparse.ArgumentParser(description="Directly seed the RAG knowledge base.")
    parser.add_argument(
        "--manifest",
        default="data/knowledge_base/manifest.json",
        help="Path to the manifest JSON file.",
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete existing chunks with matching source_id before upserting.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        logger.error("manifest not found: %s", manifest_path)
        return 2

    return asyncio.run(run(manifest_path, overwrite=args.overwrite))


if __name__ == "__main__":
    raise SystemExit(main())
