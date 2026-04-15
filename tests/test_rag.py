import pytest
from unittest.mock import AsyncMock, patch

from app.services.rag import build_rag_context, compress_sources


# --------------- build_rag_context ---------------

@pytest.mark.asyncio
async def test_build_rag_context_empty():
    with patch("app.services.rag.search_text_chunks", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        text, chunks, score = await build_rag_context(query="test", limit=5, language="ru")

    assert text == ""
    assert chunks == []
    assert score is None


@pytest.mark.asyncio
async def test_build_rag_context_formats_sources():
    fake_chunks = [
        {"score": 0.8, "text": "Vitamin D is important.", "source_id": "src1", "title": "Vitamins"},
        {"score": 0.6, "text": "Calcium helps bones.", "source_id": "src2", "title": "Minerals"},
    ]
    with patch("app.services.rag.search_text_chunks", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = fake_chunks
        text, chunks, score = await build_rag_context(query="vitamins", limit=5, language="en")

    assert "[SOURCE 1]" in text
    assert "[SOURCE 2]" in text
    assert "Vitamin D is important." in text
    assert "Calcium helps bones." in text
    assert "title: Vitamins" in text
    assert chunks == fake_chunks


@pytest.mark.asyncio
async def test_build_rag_context_calculates_avg_score():
    fake_chunks = [
        {"score": 0.8, "text": "t1", "source_id": "s1", "title": "T1"},
        {"score": 0.6, "text": "t2", "source_id": "s2", "title": "T2"},
        {"score": 0.4, "text": "t3", "source_id": "s3", "title": "T3"},
    ]
    with patch("app.services.rag.search_text_chunks", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = fake_chunks
        _, _, score = await build_rag_context(query="test", limit=5, language="ru")

    assert score == pytest.approx(0.6, abs=0.001)


@pytest.mark.asyncio
async def test_build_rag_context_passes_redis_and_fallback():
    mock_redis = AsyncMock()
    with patch("app.services.rag.search_text_chunks", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        await build_rag_context(
            query="test", limit=5, language="kk",
            redis_client=mock_redis, fallback_languages=["ru", "en"],
        )

    call_kwargs = mock_search.call_args[1]
    assert call_kwargs["redis_client"] is mock_redis
    assert call_kwargs["fallback_languages"] == ["ru", "en"]


# --------------- compress_sources ---------------

def test_compress_sources_deduplicates():
    chunks = [
        {"source_id": "s1", "title": "T1", "language": "ru", "score": 0.9},
        {"source_id": "s1", "title": "T1", "language": "ru", "score": 0.8},
        {"source_id": "s2", "title": "T2", "language": "en", "score": 0.7},
    ]
    result = compress_sources(chunks)
    assert len(result) == 2
    assert result[0]["source_id"] == "s1"
    assert result[1]["source_id"] == "s2"


def test_compress_sources_empty():
    assert compress_sources([]) == []


def test_compress_sources_preserves_score():
    chunks = [
        {"source_id": "s1", "title": "T", "language": "ru", "score": 0.85},
    ]
    result = compress_sources(chunks)
    assert result[0]["score"] == 0.85
