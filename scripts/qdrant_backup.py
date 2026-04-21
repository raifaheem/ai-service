"""Create a Qdrant collection snapshot and prune old ones.

Qdrant's snapshot API (`POST /collections/{name}/snapshots`) serializes the
collection's HNSW index + payloads into a single file under
`/qdrant/storage/snapshots/{collection}/`. That file is bit-identical across
restarts and can be downloaded over HTTP or copied from the mounted volume
for off-host backup.

Two operating modes:
- In-container: snapshots land on the mounted `qdrant-health-ai-data` volume.
  Cron job on the host tars / rclones / S3-copies that directory daily.
- With `--download-to PATH`: the script itself GETs the snapshot file and
  drops it at PATH. Useful when the backup runner doesn't share the volume
  (e.g. separate host) — pair with existing off-host transport.

Invocation:
    # Manual (uses env QDRANT_URL + QDRANT_COLLECTION)
    python scripts/qdrant_backup.py

    # Compose one-shot
    docker compose --profile backup run --rm qdrant-backup

    # With download + retention
    python scripts/qdrant_backup.py --download-to /backup/qdrant --keep-last 7
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from qdrant_client import AsyncQdrantClient

logger = logging.getLogger("qdrant_backup")


@dataclass(frozen=True)
class SnapshotInfo:
    """Trimmed projection of qdrant_client.models.SnapshotDescription.

    Kept local so retention logic can be unit-tested without dragging in
    the full client type (which varies across versions).
    """

    name: str
    creation_time: str  # ISO-8601 string as Qdrant returns it

    def sort_key(self) -> str:
        # Qdrant names include the iso timestamp, and creation_time is iso
        # too — lexical sort is chronological for both. Prefer creation_time
        # when present; fall back to name for older Qdrant versions that
        # didn't populate it.
        return self.creation_time or self.name


def snapshots_to_prune(snapshots: list[SnapshotInfo], keep_last: int) -> list[SnapshotInfo]:
    """Return snapshots older than the `keep_last` most recent.

    Pure function — drives retention without touching Qdrant. Sort is stable
    chronological; caller is responsible for actually deleting the returned
    items.
    """
    if keep_last < 0:
        raise ValueError("keep_last must be >= 0")
    if keep_last == 0:
        return list(snapshots)
    if len(snapshots) <= keep_last:
        return []
    ordered = sorted(snapshots, key=SnapshotInfo.sort_key)
    # Drop the tail (newest) — everything before it is eligible for deletion.
    return ordered[:-keep_last]


async def _download_snapshot(
    qdrant_url: str,
    collection: str,
    snapshot_name: str,
    dest_dir: Path,
    *,
    timeout: float = 300.0,
) -> Path:
    """Stream the snapshot file to `dest_dir/{snapshot_name}`.

    Uses chunked download so large snapshots don't need to fit in memory.
    Writes to a `.part` file first, renames on success — avoids leaving a
    truncated file visible to the off-host transport on failure.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / snapshot_name
    partial_path = final_path.with_suffix(final_path.suffix + ".part")

    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots/{snapshot_name}"
    logger.info("Downloading snapshot %s → %s", snapshot_name, final_path)

    async with (
        httpx.AsyncClient(timeout=timeout) as http,
        http.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        with partial_path.open("wb") as fh:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                fh.write(chunk)

    partial_path.rename(final_path)
    return final_path


async def run_backup(
    *,
    qdrant_url: str,
    collection: str,
    keep_last: int,
    download_to: Path | None,
) -> int:
    """End-to-end: create snapshot, optionally download, prune old ones.

    Returns a shell-style exit code (0 = success).
    """
    client = AsyncQdrantClient(url=qdrant_url, timeout=60.0)
    try:
        # Fail fast — a missing collection is an ops error, not a silent noop.
        await client.get_collection(collection)

        logger.info("Creating snapshot for collection '%s'", collection)
        created = await client.create_snapshot(collection_name=collection)
        if created is None or not getattr(created, "name", None):
            logger.error("Qdrant returned no snapshot description; aborting")
            return 1
        logger.info("Snapshot created: %s", created.name)

        if download_to is not None:
            await _download_snapshot(qdrant_url, collection, created.name, download_to)

        # Retention.
        raw = await client.list_snapshots(collection_name=collection)
        snapshots = [
            SnapshotInfo(name=s.name, creation_time=getattr(s, "creation_time", "") or "")
            for s in raw
            if getattr(s, "name", None)
        ]
        to_delete = snapshots_to_prune(snapshots, keep_last)
        for snap in to_delete:
            logger.info("Pruning old snapshot: %s", snap.name)
            try:
                await client.delete_snapshot(collection_name=collection, snapshot_name=snap.name)
            except Exception:
                # Retention best-effort — don't fail the whole backup because
                # one old snapshot was already removed by a parallel run.
                logger.exception("Failed to delete snapshot %s", snap.name)

        logger.info(
            "Backup complete: %d snapshot(s) total, %d pruned, latest=%s",
            len(snapshots),
            len(to_delete),
            created.name,
        )
        return 0
    finally:
        await client.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Qdrant snapshot and prune old ones (retention window).",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL (default: $QDRANT_URL or http://localhost:6333).",
    )
    parser.add_argument(
        "--collection",
        default=os.environ.get("QDRANT_COLLECTION", "medical_articles"),
        help="Collection to snapshot (default: $QDRANT_COLLECTION or medical_articles).",
    )
    parser.add_argument(
        "--keep-last",
        type=int,
        default=7,
        help="Retain the N most recent snapshots; older ones are deleted (default: 7).",
    )
    parser.add_argument(
        "--download-to",
        type=Path,
        default=None,
        help="Optional directory to download the new snapshot into (off-host backup).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(
        run_backup(
            qdrant_url=args.qdrant_url,
            collection=args.collection,
            keep_last=args.keep_last,
            download_to=args.download_to,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
