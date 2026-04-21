"""Tests for scripts/seed_direct.py (B.4).

Verifies the direct-seed path mocks out Redis/Qdrant correctly and produces
chunk payloads that carry the A.1 header metadata.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def test_seed_one_passes_header_metadata(tmp_path: Path):
    from scripts import seed_direct

    article_path = tmp_path / "headaches.md"
    article_path.write_text(
        "# Headaches\n\n"
        "Background text about headaches. "
        * 20
        + "\n\n## Red flags\n\n"
        + "Sudden severe pain warrants urgent care. " * 15
        + "\n\n## Self-care\n\n"
        + "Rest, hydrate, avoid triggers. " * 15,
        encoding="utf-8",
    )

    captured: list = []

    async def _capture_upsert(chunks, redis_client=None):
        captured.append(list(chunks))
        return len(chunks)

    async def _noop_delete(source_id):
        return 0

    with (
        patch("scripts.seed_direct.upsert_text_chunks", new=_capture_upsert),
        patch("scripts.seed_direct.delete_chunks_by_source", new=_noop_delete),
    ):
        result = await seed_direct.seed_one(
            entry={
                "source_id": "hdr-001",
                "file": "headaches.md",
                "title": "Headaches",
                "language": "en",
                "topic": "neurology",
                "attribution": {"source": "NIH"},
            },
            base_path=tmp_path,
            overwrite=True,
        )

    assert result["source_id"] == "hdr-001"
    assert result["chunks"] >= 2
    assert captured, "upsert_text_chunks was not called"
    chunks = captured[0]

    # Chunks are flat dicts with source_id/title/language/metadata.
    for c in chunks:
        assert c["source_id"] == "hdr-001"
        assert c["language"] == "en"
        assert c["metadata"]["topic"] == "neurology"
        assert "header" in c["metadata"]
        assert c["metadata"]["attribution"] == {"source": "NIH"}


async def test_run_loads_manifest_and_invokes_seed_one(tmp_path: Path):
    from scripts import seed_direct

    manifest = {
        "articles": [
            {
                "source_id": "a-1",
                "file": "a.md",
                "title": "One",
                "language": "en",
                "topic": "general",
            },
            {
                "source_id": "a-2",
                "file": "b.md",
                "title": "Two",
                "language": "en",
                "topic": "general",
            },
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "a.md").write_text("Body A. " * 30, encoding="utf-8")
    (tmp_path / "b.md").write_text("Body B. " * 30, encoding="utf-8")

    with (
        patch("scripts.seed_direct.init_redis", new_callable=AsyncMock),
        patch("scripts.seed_direct.close_redis", new_callable=AsyncMock),
        patch("scripts.seed_direct.init_qdrant", new_callable=AsyncMock),
        patch("scripts.seed_direct.close_qdrant", new_callable=AsyncMock),
        patch("scripts.seed_direct.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("scripts.seed_direct.delete_chunks_by_source", new_callable=AsyncMock, return_value=0),
        patch("scripts.seed_direct.upsert_text_chunks", new_callable=AsyncMock, return_value=2),
    ):
        exit_code = await seed_direct.run(tmp_path / "manifest.json", overwrite=True)

    assert exit_code == 0


async def test_run_returns_nonzero_when_manifest_missing_articles(tmp_path: Path):
    from scripts import seed_direct

    (tmp_path / "manifest.json").write_text(json.dumps({"articles": []}), encoding="utf-8")

    with (
        patch("scripts.seed_direct.init_redis", new_callable=AsyncMock),
        patch("scripts.seed_direct.init_qdrant", new_callable=AsyncMock),
    ):
        exit_code = await seed_direct.run(tmp_path / "manifest.json", overwrite=True)

    assert exit_code == 2
