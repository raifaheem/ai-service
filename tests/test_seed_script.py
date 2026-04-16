"""Unit tests for scripts/seed_knowledge_base.py.

All HTTP traffic is stubbed via unittest.mock (no new deps). The script is
sync, so tests don't need asyncio.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts import seed_knowledge_base as seed
from scripts._common import ServiceClient


def _write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "articles": entries}), encoding="utf-8")
    return manifest


def _write_article(tmp_path: Path, rel: str, body: str = None) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or ("Long medical content. " * 30), encoding="utf-8")
    return path


def _fake_client() -> MagicMock:
    client = MagicMock(spec=ServiceClient)
    client.analyze_article.return_value = {"indexed_chunks": 3, "extracted_chars": 1200}
    client.delete_source.return_value = {"source_id": "x", "deleted": 0}
    return client


# --------------- load_manifest ---------------


class TestLoadManifest:
    def test_parses_valid_manifest(self, tmp_path):
        manifest = _write_manifest(tmp_path, [
            {
                "source_id": "a", "file": "a.txt", "title": "A",
                "language": "ru", "topic": "symptoms", "attribution": {},
            },
        ])
        entries = seed.load_manifest(manifest)
        assert len(entries) == 1
        assert entries[0].source_id == "a"

    def test_rejects_missing_field(self, tmp_path):
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "ru"},
            # topic missing
        ])
        with pytest.raises(SystemExit, match="missing fields"):
            seed.load_manifest(manifest)

    def test_rejects_invalid_language(self, tmp_path):
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "fr", "topic": "x"},
        ])
        with pytest.raises(SystemExit, match="language must be one of"):
            seed.load_manifest(manifest)

    def test_rejects_duplicate_source_id(self, tmp_path):
        manifest = _write_manifest(tmp_path, [
            {"source_id": "dup", "file": "a.txt", "title": "A", "language": "ru", "topic": "x"},
            {"source_id": "dup", "file": "b.txt", "title": "B", "language": "en", "topic": "y"},
        ])
        with pytest.raises(SystemExit, match="duplicate source_id"):
            seed.load_manifest(manifest)

    def test_rejects_missing_manifest(self, tmp_path):
        with pytest.raises(SystemExit, match="manifest not found"):
            seed.load_manifest(tmp_path / "nope.json")

    def test_rejects_bad_json(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="not valid JSON"):
            seed.load_manifest(path)


# --------------- filter_entries ---------------


def _entry(**over) -> seed.ManifestEntry:
    defaults = dict(
        source_id="x", file="x.txt", title="X",
        language="ru", topic="symptoms", attribution={},
    )
    defaults.update(over)
    return seed.ManifestEntry(**defaults)


class TestFilterEntries:
    def test_filters_by_language(self):
        entries = [_entry(source_id="a", language="ru"), _entry(source_id="b", language="en")]
        result = seed.filter_entries(entries, language="en", topic=None)
        assert [e.source_id for e in result] == ["b"]

    def test_filters_by_topic(self):
        entries = [
            _entry(source_id="a", topic="nutrition"),
            _entry(source_id="b", topic="symptoms"),
        ]
        result = seed.filter_entries(entries, language=None, topic="nutrition")
        assert [e.source_id for e in result] == ["a"]

    def test_no_filters_returns_all(self):
        entries = [_entry(source_id="a"), _entry(source_id="b")]
        assert len(seed.filter_entries(entries, language=None, topic=None)) == 2


# --------------- read_article_body ---------------


class TestReadArticleBody:
    def test_reads_body(self, tmp_path):
        _write_article(tmp_path, "articles/a.txt")
        entry = _entry(file="articles/a.txt")
        text = seed.read_article_body(tmp_path, entry)
        assert len(text) >= 200

    def test_rejects_missing_file(self, tmp_path):
        entry = _entry(file="missing.txt")
        with pytest.raises(FileNotFoundError):
            seed.read_article_body(tmp_path, entry)

    def test_rejects_too_short(self, tmp_path):
        _write_article(tmp_path, "a.txt", body="short")
        entry = _entry(file="a.txt")
        with pytest.raises(ValueError, match="too short"):
            seed.read_article_body(tmp_path, entry)


# --------------- seed_one ---------------


class TestSeedOne:
    def test_happy_path(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        ok, chunks, msg = seed.seed_one(
            client=client, manifest_dir=tmp_path, entry=entry,
            overwrite=False, dry_run=False,
        )

        assert ok is True
        assert chunks == 3
        assert "indexed 3 chunks" in msg
        client.analyze_article.assert_called_once()
        client.delete_source.assert_not_called()

    def test_overwrite_deletes_first(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        seed.seed_one(
            client=client, manifest_dir=tmp_path, entry=entry,
            overwrite=True, dry_run=False,
        )

        client.delete_source.assert_called_once_with("a")
        client.analyze_article.assert_called_once()

    def test_dry_run_makes_no_calls(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        ok, chunks, msg = seed.seed_one(
            client=client, manifest_dir=tmp_path, entry=entry,
            overwrite=True, dry_run=True,
        )

        assert ok is True
        assert chunks == 0
        assert "dry-run" in msg
        client.analyze_article.assert_not_called()
        client.delete_source.assert_not_called()

    def test_returns_failure_on_http_error(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        resp = httpx.Response(422, text="bad payload", request=httpx.Request("POST", "/x"))
        client.analyze_article.side_effect = httpx.HTTPStatusError("422", request=resp.request, response=resp)

        ok, chunks, msg = seed.seed_one(
            client=client, manifest_dir=tmp_path, entry=entry,
            overwrite=False, dry_run=False,
        )

        assert ok is False
        assert chunks == 0
        assert "HTTP 422" in msg

    def test_returns_failure_on_missing_file(self, tmp_path):
        entry = _entry(source_id="a", file="missing.txt")
        client = _fake_client()
        ok, _, msg = seed.seed_one(
            client=client, manifest_dir=tmp_path, entry=entry,
            overwrite=False, dry_run=False,
        )
        assert ok is False
        assert "missing" in msg


# --------------- run + summary ---------------


class TestRun:
    def test_end_to_end_dry_run(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        _write_article(tmp_path, "b.txt")
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "ru", "topic": "symptoms"},
            {"source_id": "b", "file": "b.txt", "title": "B", "language": "en", "topic": "nutrition"},
        ])

        summary = seed.run(
            manifest_path=manifest, base_url="http://unused",
            overwrite=True, only_language=None, only_topic=None, dry_run=True,
        )

        assert len(summary.ok) == 2
        assert len(summary.failed) == 0
        assert summary.exit_code() == 0

    def test_records_failures(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "ru", "topic": "symptoms"},
            # file missing on disk
            {"source_id": "b", "file": "does-not-exist.txt", "title": "B", "language": "en", "topic": "x"},
        ])

        summary = seed.run(
            manifest_path=manifest, base_url="http://unused",
            overwrite=False, only_language=None, only_topic=None, dry_run=True,
        )

        assert len(summary.ok) == 1
        assert len(summary.failed) == 1
        assert summary.exit_code() == 1

    def test_language_filter_narrows_set(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        _write_article(tmp_path, "b.txt")
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "ru", "topic": "x"},
            {"source_id": "b", "file": "b.txt", "title": "B", "language": "en", "topic": "x"},
        ])

        summary = seed.run(
            manifest_path=manifest, base_url="http://unused",
            overwrite=False, only_language="ru", only_topic=None, dry_run=True,
        )

        assert summary.ok == ["a"]


# --------------- CLI ---------------


class TestMain:
    def test_main_returns_exit_code_from_run(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "a.txt", "title": "A", "language": "ru", "topic": "x"},
        ])

        rc = seed.main(["--manifest", str(manifest), "--dry-run"])
        assert rc == 0

    def test_main_exits_nonzero_on_failure(self, tmp_path):
        manifest = _write_manifest(tmp_path, [
            {"source_id": "a", "file": "missing.txt", "title": "A", "language": "ru", "topic": "x"},
        ])
        rc = seed.main(["--manifest", str(manifest), "--dry-run"])
        assert rc == 1


# --------------- retry_with_backoff wired in (via public seed_one path) ---------------


class TestRetryBehavior:
    def test_retries_on_5xx_then_succeeds(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        # First call fails with 500, second succeeds
        error_resp = httpx.Response(503, text="down", request=httpx.Request("POST", "/x"))
        client.analyze_article.side_effect = [
            httpx.HTTPStatusError("503", request=error_resp.request, response=error_resp),
            {"indexed_chunks": 2},
        ]

        with patch("scripts.seed_knowledge_base.time.sleep"):  # don't wait in tests
            # seed_knowledge_base imports retry_with_backoff via _common; patch time there
            with patch("scripts._common.time.sleep"):
                ok, chunks, _ = seed.seed_one(
                    client=client, manifest_dir=tmp_path, entry=entry,
                    overwrite=False, dry_run=False,
                )

        assert ok is True
        assert chunks == 2
        assert client.analyze_article.call_count == 2

    def test_does_not_retry_on_4xx(self, tmp_path):
        _write_article(tmp_path, "a.txt")
        entry = _entry(source_id="a", file="a.txt")
        client = _fake_client()

        error_resp = httpx.Response(400, text="bad", request=httpx.Request("POST", "/x"))
        client.analyze_article.side_effect = httpx.HTTPStatusError(
            "400", request=error_resp.request, response=error_resp,
        )

        with patch("scripts._common.time.sleep"):
            ok, _, _ = seed.seed_one(
                client=client, manifest_dir=tmp_path, entry=entry,
                overwrite=False, dry_run=False,
            )

        assert ok is False
        assert client.analyze_article.call_count == 1
