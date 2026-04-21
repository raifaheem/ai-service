"""[DEPRECATED — prefer scripts/seed_direct.py]

Bulk-seed the RAG knowledge base from a declarative manifest via HTTP.

This script pushes every manifest entry through `POST /v1/articles/analyze`,
which requires `ENABLE_DEV_ROUTES=true` — but that flag is forbidden in
production by app/config.py::_validate_prod_safety, so this script can't be
used against prod. Use scripts/seed_direct.py instead (or the `seed` docker
compose profile, which runs seed_direct.py in the internal network).

Kept here for legacy local workflows that already shell out to it.

Example:
    python scripts/seed_knowledge_base.py \\
        --manifest data/knowledge_base/manifest.json \\
        --base-url http://localhost:8001

Idempotent: when `--overwrite` is set (default), any existing chunks with the
same `source_id` are removed from Qdrant before the article is re-inserted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import (
    Progress,
    ServiceClient,
    configure_logging,
    load_service_token,
    logger,
    retry_with_backoff,
)

DEFAULT_MANIFEST = Path("data/knowledge_base/manifest.json")
DEFAULT_BASE_URL = "http://localhost:8001"


@dataclass
class ManifestEntry:
    source_id: str
    file: str
    title: str
    language: str
    topic: str
    attribution: dict

    @classmethod
    def from_dict(cls, raw: dict) -> ManifestEntry:
        required = ["source_id", "file", "title", "language", "topic"]
        missing = [k for k in required if k not in raw]
        if missing:
            raise ValueError(f"manifest entry missing fields {missing}: {raw}")
        if raw["language"] not in ("ru", "en", "kk"):
            raise ValueError(
                f"manifest entry {raw['source_id']}: language must be one of ru/en/kk, got {raw['language']!r}"
            )
        return cls(
            source_id=str(raw["source_id"]),
            file=str(raw["file"]),
            title=str(raw["title"]),
            language=str(raw["language"]),
            topic=str(raw["topic"]),
            attribution=dict(raw.get("attribution", {})),
        )


@dataclass
class SeedSummary:
    ok: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_chunks: int = 0
    elapsed_seconds: float = 0.0

    def exit_code(self) -> int:
        return 0 if not self.failed else 1


def load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        raise SystemExit(f"manifest not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"manifest {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict) or "articles" not in raw:
        raise SystemExit("manifest must be an object with an 'articles' key")

    entries: list[ManifestEntry] = []
    seen_source_ids: set[str] = set()
    for idx, item in enumerate(raw["articles"]):
        try:
            entry = ManifestEntry.from_dict(item)
        except ValueError as exc:
            raise SystemExit(f"manifest entry #{idx}: {exc}") from exc
        if entry.source_id in seen_source_ids:
            raise SystemExit(f"duplicate source_id in manifest: {entry.source_id!r}")
        seen_source_ids.add(entry.source_id)
        entries.append(entry)
    return entries


def filter_entries(
    entries: list[ManifestEntry],
    *,
    language: str | None,
    topic: str | None,
) -> list[ManifestEntry]:
    out = entries
    if language:
        out = [e for e in out if e.language == language]
    if topic:
        out = [e for e in out if e.topic == topic]
    return out


def read_article_body(manifest_dir: Path, entry: ManifestEntry) -> str:
    path = (manifest_dir / entry.file).resolve()
    if not path.exists():
        raise FileNotFoundError(f"article file missing for {entry.source_id}: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if len(text) < 200:
        raise ValueError(
            f"{entry.source_id}: article too short ({len(text)} chars); " "the analyzer requires ≥200 characters"
        )
    return text


def seed_one(
    client: ServiceClient,
    manifest_dir: Path,
    entry: ManifestEntry,
    *,
    overwrite: bool,
    dry_run: bool,
) -> tuple[bool, int, str]:
    """Returns (ok, chunks_indexed, message)."""
    try:
        text = read_article_body(manifest_dir, entry)
    except (FileNotFoundError, ValueError) as exc:
        return False, 0, str(exc)

    if dry_run:
        return True, 0, f"dry-run: would index {len(text)} chars"

    if overwrite:
        try:
            retry_with_backoff(
                lambda: client.delete_source(entry.source_id),
                label=f"delete {entry.source_id}",
            )
        except httpx.HTTPStatusError as exc:
            # 404 or similar — keep going; delete is best-effort
            logger.debug("delete_source %s: HTTP %s", entry.source_id, exc.response.status_code)

    try:
        result = retry_with_backoff(
            lambda: client.analyze_article(
                title=entry.title,
                text=text,
                language=entry.language,
                source_id=entry.source_id,
            ),
            label=f"analyze {entry.source_id}",
        )
    except httpx.HTTPStatusError as exc:
        return False, 0, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return False, 0, f"transport error: {exc}"

    indexed = int(result.get("indexed_chunks", 0))
    return True, indexed, f"indexed {indexed} chunks"


def run(
    *,
    manifest_path: Path,
    base_url: str,
    overwrite: bool,
    only_language: str | None,
    only_topic: str | None,
    dry_run: bool,
) -> SeedSummary:
    started = time.perf_counter()
    summary = SeedSummary()

    entries = load_manifest(manifest_path)
    entries = filter_entries(entries, language=only_language, topic=only_topic)
    if not entries:
        logger.warning("no manifest entries matched filters — nothing to seed")
        summary.elapsed_seconds = time.perf_counter() - started
        return summary

    logger.info(
        "seeding %d articles from %s (base=%s, overwrite=%s, dry_run=%s)",
        len(entries),
        manifest_path,
        base_url,
        overwrite,
        dry_run,
    )

    manifest_dir = manifest_path.resolve().parent

    if dry_run:
        service_token = "dry-run"
        client = None  # not used
    else:
        service_token = load_service_token()
        client = ServiceClient(base_url, service_token)

    progress = Progress(total=len(entries))
    try:
        for entry in entries:
            ok, chunks, message = seed_one(
                client=client,  # type: ignore[arg-type]
                manifest_dir=manifest_dir,
                entry=entry,
                overwrite=overwrite,
                dry_run=dry_run,
            )
            if ok:
                summary.ok.append(entry.source_id)
                summary.total_chunks += chunks
            else:
                summary.failed.append((entry.source_id, message))
                logger.error("%s failed: %s", entry.source_id, message)
            progress.update(f"{entry.language} {entry.source_id}  {message}")
        progress.done()
    finally:
        if client is not None:
            client.close()

    summary.elapsed_seconds = time.perf_counter() - started
    return summary


def print_summary(summary: SeedSummary) -> None:
    total = len(summary.ok) + len(summary.failed)
    print()
    print(f"Seed complete: {len(summary.ok)}/{total} ok, {len(summary.failed)} failed")
    print(f"  Total chunks indexed: {summary.total_chunks}")
    print(f"  Elapsed: {summary.elapsed_seconds:.1f}s")
    if summary.failed:
        print("  Failures:")
        for source_id, reason in summary.failed:
            print(f"    - {source_id}: {reason}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Seed the health-ai-service RAG knowledge base.")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to manifest.json")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="FastAPI service URL")
    p.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete existing chunks with the same source_id before inserting (default: on)",
    )
    p.add_argument("--only-language", choices=("ru", "en", "kk"), help="Seed only this locale")
    p.add_argument("--only-topic", help="Seed only entries with this `topic` value")
    p.add_argument("--dry-run", action="store_true", help="Print what would be seeded without calling the service")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    summary = run(
        manifest_path=args.manifest,
        base_url=args.base_url,
        overwrite=args.overwrite,
        only_language=args.only_language,
        only_topic=args.only_topic,
        dry_run=args.dry_run,
    )
    print_summary(summary)
    return summary.exit_code()


if __name__ == "__main__":
    sys.exit(main())
