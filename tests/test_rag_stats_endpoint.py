"""Tests for the dev endpoints added in Phase 12.

Covers:
  - GET /v1/rag/stats → aggregation shape + auth
  - DELETE /v1/rag/source/{id} → delete-by-filter flow
  - Service-level helpers (collect_corpus_stats, delete_chunks_by_source)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.vector_store import collect_corpus_stats, delete_chunks_by_source


# --------------- service helpers ---------------


class TestCollectCorpusStats:
    async def test_aggregates_single_page(self):
        p1 = MagicMock()
        p1.payload = {"language": "ru", "source_id": "a"}
        p2 = MagicMock()
        p2.payload = {"language": "ru", "source_id": "b"}
        p3 = MagicMock()
        p3.payload = {"language": "en", "source_id": "a"}  # same source_id in different lang

        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(return_value=([p1, p2, p3], None))

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            stats = await collect_corpus_stats()

        assert stats["total_chunks"] == 3
        assert stats["by_language"] == {"ru": 2, "en": 1}
        assert stats["sources_by_language"] == {"ru": 2, "en": 1}
        assert stats["total_sources"] == 2  # "a" is dedup'd across languages

    async def test_paginates_until_exhausted(self):
        p1 = MagicMock()
        p1.payload = {"language": "ru", "source_id": "a"}
        p2 = MagicMock()
        p2.payload = {"language": "en", "source_id": "b"}

        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(
            side_effect=[([p1], "offset-1"), ([p2], None)]
        )

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            stats = await collect_corpus_stats()

        assert stats["total_chunks"] == 2
        assert mock_client.scroll.await_count == 2

    async def test_handles_missing_payload_fields(self):
        p = MagicMock()
        p.payload = {}  # no language, no source_id

        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(return_value=([p], None))

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            stats = await collect_corpus_stats()

        assert stats["total_chunks"] == 1
        assert stats["by_language"] == {"unknown": 1}
        assert stats["total_sources"] == 0

    async def test_empty_collection(self):
        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(return_value=([], None))

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            stats = await collect_corpus_stats()

        assert stats["total_chunks"] == 0
        assert stats["by_language"] == {}
        assert stats["total_sources"] == 0


class TestDeleteChunksBySource:
    async def test_deletes_when_matches_exist(self):
        mock_client = AsyncMock()
        count_result = MagicMock()
        count_result.count = 5
        mock_client.count = AsyncMock(return_value=count_result)
        mock_client.delete = AsyncMock()

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            deleted = await delete_chunks_by_source("who-headache-2024")

        assert deleted == 5
        mock_client.delete.assert_awaited_once()

    async def test_skips_delete_when_no_matches(self):
        mock_client = AsyncMock()
        count_result = MagicMock()
        count_result.count = 0
        mock_client.count = AsyncMock(return_value=count_result)
        mock_client.delete = AsyncMock()

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client):
            deleted = await delete_chunks_by_source("nonexistent")

        assert deleted == 0
        mock_client.delete.assert_not_awaited()


# --------------- HTTP endpoints ---------------


def _auth_headers() -> dict:
    return {"X-Service-Token": "test-token"}


class TestStatsEndpoint:
    def test_returns_aggregated_stats(self):
        fake_stats = {
            "collection": "medical_articles",
            "total_chunks": 120,
            "total_sources": 30,
            "by_language": {"ru": 42, "en": 40, "kk": 38},
            "sources_by_language": {"ru": 10, "en": 10, "kk": 10},
        }
        with patch(
            "app.routers.rag.collect_corpus_stats",
            new=AsyncMock(return_value=fake_stats),
        ):
            from app.main import app

            client = TestClient(app)
            resp = client.get("/v1/rag/stats", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json() == fake_stats

    def test_requires_auth(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/v1/rag/stats")  # no header
        assert resp.status_code == 401


class TestDeleteSourceEndpoint:
    def test_deletes_and_returns_count(self):
        with patch(
            "app.routers.rag.delete_chunks_by_source",
            new=AsyncMock(return_value=7),
        ):
            from app.main import app

            client = TestClient(app)
            resp = client.delete("/v1/rag/source/who-headache-2024", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json() == {"source_id": "who-headache-2024", "deleted": 7}

    def test_returns_zero_for_missing_source(self):
        with patch(
            "app.routers.rag.delete_chunks_by_source",
            new=AsyncMock(return_value=0),
        ):
            from app.main import app

            client = TestClient(app)
            resp = client.delete("/v1/rag/source/does-not-exist", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    def test_requires_auth(self):
        from app.main import app

        client = TestClient(app)
        resp = client.delete("/v1/rag/source/x")
        assert resp.status_code == 401


# Dev-route gating is a one-line include_router guard in app/main.py:
#   `if settings.enable_dev_routes: app.include_router(rag_router)`
# Exercising it dynamically would require reloading the whole app, which is
# brittle across test ordering. The static check covers it.
