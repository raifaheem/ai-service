from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest

from app.services.vector_store import (
    _extract_collection_vector_size,
    _qdrant_results_to_items,
)


# --------------- _extract_collection_vector_size ---------------

class TestExtractCollectionVectorSize:
    def test_returns_size(self):
        info = MagicMock()
        info.config.params.vectors.size = 1536
        assert _extract_collection_vector_size(info) == 1536

    def test_returns_none_when_no_config(self):
        info = MagicMock(spec=[])  # no config attribute
        assert _extract_collection_vector_size(info) is None

    def test_returns_none_when_no_params(self):
        info = MagicMock()
        info.config = MagicMock(spec=[])  # no params attribute
        assert _extract_collection_vector_size(info) is None


# --------------- _qdrant_results_to_items ---------------

class TestQdrantResultsToItems:
    def test_converts_results(self):
        result = MagicMock()
        result.id = "point-1"
        result.score = 0.92
        result.payload = {
            "text": "Medical article text",
            "source_id": "src-1",
            "title": "Article Title",
            "language": "en",
            "metadata": {"author": "Dr. Test"},
        }

        items = _qdrant_results_to_items([result])
        assert len(items) == 1
        assert items[0]["id"] == "point-1"
        assert items[0]["score"] == 0.92
        assert items[0]["text"] == "Medical article text"
        assert items[0]["source_id"] == "src-1"
        assert items[0]["title"] == "Article Title"

    def test_empty_results(self):
        items = _qdrant_results_to_items([])
        assert items == []

    def test_missing_payload_fields(self):
        result = MagicMock()
        result.id = "point-2"
        result.score = 0.5
        result.payload = {}

        items = _qdrant_results_to_items([result])
        assert items[0]["text"] == ""
        assert items[0]["source_id"] == ""
        assert items[0]["title"] is None

    def test_none_payload(self):
        result = MagicMock()
        result.id = "point-3"
        result.score = 0.3
        result.payload = None

        items = _qdrant_results_to_items([result])
        assert items[0]["text"] == ""
        assert items[0]["metadata"] == {}

    def test_multiple_results(self):
        results = []
        for i in range(3):
            r = MagicMock()
            r.id = f"point-{i}"
            r.score = 0.9 - i * 0.1
            r.payload = {"text": f"text {i}", "source_id": f"src-{i}"}
            results.append(r)

        items = _qdrant_results_to_items(results)
        assert len(items) == 3
        assert items[0]["score"] > items[1]["score"] > items[2]["score"]


# --------------- ensure_qdrant_collection ---------------

class TestEnsureQdrantCollection:
    async def test_creates_collection_if_not_exists(self):
        mock_client = AsyncMock()
        collections_response = MagicMock()
        collections_response.collections = []
        mock_client.get_collections = AsyncMock(return_value=collections_response)
        mock_client.create_collection = AsyncMock()

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.get_embedding_dimension", return_value=1536):
            from app.services.vector_store import ensure_qdrant_collection
            await ensure_qdrant_collection()

        mock_client.create_collection.assert_called_once()

    async def test_skips_if_collection_exists_with_correct_size(self):
        mock_client = AsyncMock()

        col = MagicMock()
        col.name = "medical_articles"
        collections_response = MagicMock()
        collections_response.collections = [col]
        mock_client.get_collections = AsyncMock(return_value=collections_response)

        info = MagicMock()
        info.config.params.vectors.size = 1536
        mock_client.get_collection = AsyncMock(return_value=info)

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.get_embedding_dimension", return_value=1536):
            from app.services.vector_store import ensure_qdrant_collection
            await ensure_qdrant_collection()

        mock_client.create_collection.assert_not_called()

    async def test_raises_if_size_mismatch(self):
        mock_client = AsyncMock()

        col = MagicMock()
        col.name = "medical_articles"
        collections_response = MagicMock()
        collections_response.collections = [col]
        mock_client.get_collections = AsyncMock(return_value=collections_response)

        info = MagicMock()
        info.config.params.vectors.size = 768
        mock_client.get_collection = AsyncMock(return_value=info)

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.get_embedding_dimension", return_value=1536):
            from app.services.vector_store import ensure_qdrant_collection
            with pytest.raises(RuntimeError, match="vector size"):
                await ensure_qdrant_collection()


# --------------- upsert_text_chunks ---------------

class TestUpsertTextChunks:
    async def test_upserts_chunks(self):
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        chunks = [
            {"text": "First chunk", "source_id": "src-1", "title": "Article", "language": "en"},
            {"text": "Second chunk", "source_id": "src-1", "title": "Article", "language": "en"},
        ]

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_texts", new_callable=AsyncMock, return_value=[[0.1]*10, [0.2]*10]):
            from app.services.vector_store import upsert_text_chunks
            count = await upsert_text_chunks(chunks)

        assert count == 2
        mock_client.upsert.assert_called_once()

    async def test_empty_chunks(self):
        from app.services.vector_store import upsert_text_chunks
        count = await upsert_text_chunks([])
        assert count == 0

    async def test_filters_blank_chunks(self):
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        chunks = [
            {"text": "   ", "source_id": "src-1"},
            {"text": "Valid text", "source_id": "src-2"},
        ]

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_texts", new_callable=AsyncMock, return_value=[[0.1]*10]):
            from app.services.vector_store import upsert_text_chunks
            count = await upsert_text_chunks(chunks)

        assert count == 1


# --------------- search_text_chunks ---------------

class TestSearchTextChunks:
    async def test_basic_search(self):
        mock_client = AsyncMock()
        search_result = MagicMock()
        search_result.id = "p1"
        search_result.score = 0.9
        search_result.payload = {"text": "Result text", "source_id": "src-1"}
        mock_client.search = AsyncMock(return_value=[search_result])

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_text", new_callable=AsyncMock, return_value=[0.1]*10):
            from app.services.vector_store import search_text_chunks
            results = await search_text_chunks("headache treatment")

        assert len(results) == 1
        assert results[0]["text"] == "Result text"

    async def test_search_with_language_filter(self):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=[])

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_text", new_callable=AsyncMock, return_value=[0.1]*10):
            from app.services.vector_store import search_text_chunks
            await search_text_chunks("test", language="en")

        call_args = mock_client.search.call_args
        assert call_args.kwargs["query_filter"] is not None

    async def test_score_threshold_filters(self):
        mock_client = AsyncMock()
        high_score = MagicMock()
        high_score.id = "p1"
        high_score.score = 0.9
        high_score.payload = {"text": "High", "source_id": "s1"}

        low_score = MagicMock()
        low_score.id = "p2"
        low_score.score = 0.1
        low_score.payload = {"text": "Low", "source_id": "s2"}

        mock_client.search = AsyncMock(return_value=[high_score, low_score])

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_text", new_callable=AsyncMock, return_value=[0.1]*10):
            from app.services.vector_store import search_text_chunks
            results = await search_text_chunks("test")

        assert len(results) == 1
        assert results[0]["text"] == "High"

    async def test_fallback_languages(self):
        mock_client = AsyncMock()
        # First search returns no results (after threshold filter), second returns results
        result = MagicMock()
        result.id = "p1"
        result.score = 0.9
        result.payload = {"text": "Fallback", "source_id": "s1"}
        mock_client.search = AsyncMock(side_effect=[[], [result]])

        with patch("app.services.vector_store.get_qdrant", return_value=mock_client), \
             patch("app.services.vector_store.embed_text", new_callable=AsyncMock, return_value=[0.1]*10):
            from app.services.vector_store import search_text_chunks
            results = await search_text_chunks("test", language="kk", fallback_languages=["ru", "en"])

        assert mock_client.search.call_count == 2
        assert len(results) == 1
