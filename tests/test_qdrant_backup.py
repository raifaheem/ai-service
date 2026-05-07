"""Tests for scripts/qdrant_backup.py.

Split into:
- Pure retention logic (`snapshots_to_prune`) — deterministic, no I/O.
- CLI argument parsing — defaults + env-var wiring.
- End-to-end `run_backup` with a mocked AsyncQdrantClient.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# scripts/ is not a package — wire it into sys.path once so import works from pytest.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from qdrant_backup import (  # noqa: E402
    SnapshotInfo,
    _parse_args,
    run_backup,
    snapshots_to_prune,
)


def _snap(name: str, ts: str = "") -> SnapshotInfo:
    return SnapshotInfo(name=name, creation_time=ts)


# --------------- snapshots_to_prune ---------------


class TestSnapshotsToPrune:
    def test_empty_list_returns_empty(self):
        assert snapshots_to_prune([], keep_last=7) == []

    def test_fewer_than_keep_last_returns_empty(self):
        snaps = [_snap(f"s{i}", f"2026-04-0{i}T00:00:00Z") for i in range(1, 4)]
        assert snapshots_to_prune(snaps, keep_last=7) == []

    def test_keeps_most_recent_by_creation_time(self):
        snaps = [
            _snap("old", "2026-04-01T00:00:00Z"),
            _snap("mid", "2026-04-10T00:00:00Z"),
            _snap("new", "2026-04-20T00:00:00Z"),
            _snap("newest", "2026-04-21T00:00:00Z"),
        ]
        to_delete = snapshots_to_prune(snaps, keep_last=2)
        # Retains "new" and "newest"; deletes "old" and "mid".
        names = {s.name for s in to_delete}
        assert names == {"old", "mid"}

    def test_sort_is_chronological_not_insertion_order(self):
        # Deliberately out-of-order inputs — the function must sort by timestamp.
        snaps = [
            _snap("a", "2026-04-20T00:00:00Z"),
            _snap("b", "2026-04-01T00:00:00Z"),
            _snap("c", "2026-04-10T00:00:00Z"),
        ]
        to_delete = snapshots_to_prune(snaps, keep_last=1)
        assert {s.name for s in to_delete} == {"b", "c"}  # only "a" (newest) kept

    def test_keep_last_zero_prunes_everything(self):
        snaps = [_snap("x", "2026-04-01T00:00:00Z"), _snap("y", "2026-04-02T00:00:00Z")]
        assert {s.name for s in snapshots_to_prune(snaps, keep_last=0)} == {"x", "y"}

    def test_keep_last_negative_raises(self):
        with pytest.raises(ValueError):
            snapshots_to_prune([], keep_last=-1)

    def test_falls_back_to_name_when_no_creation_time(self):
        # Older Qdrant versions didn't return creation_time. Names include
        # the iso timestamp, so lexical sort on name is still chronological.
        snaps = [
            _snap("snap-2026-04-01-000000"),
            _snap("snap-2026-04-20-000000"),
            _snap("snap-2026-04-10-000000"),
        ]
        to_delete = snapshots_to_prune(snaps, keep_last=1)
        assert {s.name for s in to_delete} == {
            "snap-2026-04-01-000000",
            "snap-2026-04-10-000000",
        }


# --------------- _parse_args ---------------


class TestParseArgs:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("QDRANT_URL", "http://qdrant.example:6333")
        monkeypatch.setenv("QDRANT_COLLECTION", "custom_coll")
        args = _parse_args([])
        assert args.qdrant_url == "http://qdrant.example:6333"
        assert args.collection == "custom_coll"
        assert args.keep_last == 7
        assert args.download_to is None
        assert args.verbose is False

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("QDRANT_URL", "http://env:6333")
        args = _parse_args(
            ["--qdrant-url", "http://cli:6333", "--collection", "c", "--keep-last", "3"]
        )
        assert args.qdrant_url == "http://cli:6333"
        assert args.collection == "c"
        assert args.keep_last == 3

    def test_download_to_parsed_as_path(self):
        args = _parse_args(["--download-to", "/backup/qdrant"])
        assert isinstance(args.download_to, Path)
        # PosixPath on linux, WindowsPath on win — compare via Path to normalize.
        assert args.download_to == Path("/backup/qdrant")

    def test_fallback_defaults_without_env(self, monkeypatch):
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_COLLECTION", raising=False)
        args = _parse_args([])
        assert args.qdrant_url == "http://localhost:6333"
        assert args.collection == "medical_articles"


# --------------- run_backup (integration with mocked client) ---------------


def _mock_client_factory(*, snapshots_before: list[SimpleNamespace], created_name: str, points_count: int = 100):
    """Build a MagicMock standing in for AsyncQdrantClient.

    `snapshots_before` is what list_snapshots returns AFTER create_snapshot is
    called — i.e. the full set including the newly created one. Tests can
    wire any retention scenario this way. `points_count=0` lets you exercise
    the L5 empty-collection skip path.
    """
    client = MagicMock()
    client.get_collection = AsyncMock(
        return_value=SimpleNamespace(status="green", points_count=points_count)
    )
    client.create_snapshot = AsyncMock(
        return_value=SimpleNamespace(name=created_name, creation_time="2026-04-21T00:00:00Z")
    )
    client.list_snapshots = AsyncMock(return_value=snapshots_before)
    client.delete_snapshot = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    return client


@pytest.mark.asyncio
async def test_run_backup_creates_and_retains(monkeypatch):
    all_snaps = [
        SimpleNamespace(name="old1", creation_time="2026-03-01T00:00:00Z"),
        SimpleNamespace(name="old2", creation_time="2026-03-15T00:00:00Z"),
        SimpleNamespace(name="new-2026-04-21", creation_time="2026-04-21T00:00:00Z"),
    ]
    client = _mock_client_factory(snapshots_before=all_snaps, created_name="new-2026-04-21")

    with patch("qdrant_backup.AsyncQdrantClient", return_value=client):
        rc = await run_backup(
            qdrant_url="http://q:6333",
            collection="medical_articles",
            keep_last=1,
            download_to=None,
        )

    assert rc == 0
    client.create_snapshot.assert_awaited_once_with(collection_name="medical_articles")
    # keep_last=1 means we retain "new-2026-04-21" and delete old1 + old2.
    deleted_names = {call.kwargs["snapshot_name"] for call in client.delete_snapshot.await_args_list}
    assert deleted_names == {"old1", "old2"}


@pytest.mark.asyncio
async def test_run_backup_fails_fast_on_missing_collection():
    client = MagicMock()
    client.get_collection = AsyncMock(side_effect=RuntimeError("not found"))
    client.create_snapshot = AsyncMock()
    client.close = AsyncMock()

    with patch("qdrant_backup.AsyncQdrantClient", return_value=client):
        with pytest.raises(RuntimeError, match="not found"):
            await run_backup(
                qdrant_url="http://q:6333",
                collection="missing",
                keep_last=7,
                download_to=None,
            )

    client.create_snapshot.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_backup_skips_delete_errors_gracefully(caplog):
    """A second backup runner may have already deleted an old snapshot. That
    should not fail the current run — retention is best-effort."""
    all_snaps = [
        SimpleNamespace(name="gone", creation_time="2026-03-01T00:00:00Z"),
        SimpleNamespace(name="new", creation_time="2026-04-21T00:00:00Z"),
    ]
    client = _mock_client_factory(snapshots_before=all_snaps, created_name="new")
    client.delete_snapshot = AsyncMock(side_effect=RuntimeError("already gone"))

    with patch("qdrant_backup.AsyncQdrantClient", return_value=client):
        rc = await run_backup(
            qdrant_url="http://q:6333",
            collection="medical_articles",
            keep_last=1,
            download_to=None,
        )

    assert rc == 0  # non-fatal
    # Still attempted the delete.
    client.delete_snapshot.assert_awaited()


@pytest.mark.asyncio
async def test_run_backup_skips_when_collection_empty(caplog):
    """L5: an empty collection (0 points) shouldn't yield a snapshot — the
    retention window would otherwise fill with 7 useless empty files and
    mask the gap until someone notices the KB never got seeded."""
    import logging

    client = _mock_client_factory(snapshots_before=[], created_name="never-created", points_count=0)

    caplog.set_level(logging.WARNING, logger="qdrant_backup")
    with patch("qdrant_backup.AsyncQdrantClient", return_value=client):
        rc = await run_backup(
            qdrant_url="http://q:6333",
            collection="medical_articles",
            keep_last=7,
            download_to=None,
        )

    assert rc == 0  # not an error — just a no-op with a warning
    client.create_snapshot.assert_not_awaited()
    client.list_snapshots.assert_not_awaited()
    assert any("0 points" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_backup_returns_error_when_snapshot_has_no_name():
    client = MagicMock()
    client.get_collection = AsyncMock(return_value=SimpleNamespace(points_count=10))
    client.create_snapshot = AsyncMock(return_value=SimpleNamespace(name=None))
    client.list_snapshots = AsyncMock(return_value=[])
    client.delete_snapshot = AsyncMock()
    client.close = AsyncMock()

    with patch("qdrant_backup.AsyncQdrantClient", return_value=client):
        rc = await run_backup(
            qdrant_url="http://q:6333",
            collection="medical_articles",
            keep_last=7,
            download_to=None,
        )

    assert rc == 1
    client.delete_snapshot.assert_not_awaited()
