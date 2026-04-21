from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

_FAKE_ANALYSIS = {
    "summary": "Test summary",
    "key_findings": ["Finding 1", "Finding 2"],
    "limitations": ["Limitation 1"],
    "practical_meaning": ["Meaning 1"],
    "red_flags": ["Red flag 1"],
    "confidence": "medium",
}


def test_analyze_file_txt():
    client = TestClient(app)

    with (
        patch("app.routers.articles.upsert_text_chunks", new=AsyncMock(return_value=1)),
        patch("app.routers.articles.analyze_article_text", new=AsyncMock(return_value=_FAKE_ANALYSIS)),
    ):
        response = client.post(
            "/v1/articles/analyze-file",
            headers={"X-Service-Token": "test-token"},
            files={
                "file": ("sample.txt", b"Migraine " * 50, "text/plain"),
            },
            data={
                "title": "Test upload",
                "language": "en",
                "index_chunks": "true",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test upload"
    assert data["language"] == "en"
    assert data["indexed_chunks"] == 1
    assert data["extracted_chars"] > 0
    assert data["summary"] == "Test summary"


def test_analyze_article_passes_headers_to_upsert():
    """Articles with markdown headers yield chunk payloads where metadata.header is set.

    Verifies the A.1 migration to chunk_article_with_headers: each vector-chunk dict
    passed to upsert_text_chunks carries the section header its text was extracted from,
    so RAG sources can later reference the section.
    """
    client = TestClient(app)

    article_body = (
        "# Headache overview\n\n"
        "Headaches are common. "
        * 30
        + "\n\n## Red flags\n\n"
        + "Call a doctor if pain is sudden and severe. " * 20
        + "\n\n## Self-care\n\n"
        + "Drink water, rest in a dark room. " * 20
        + "\n\n## When to see a doctor\n\n"
        + "If symptoms persist beyond 72 hours. " * 20
    )

    captured_chunks: list[list[dict]] = []

    async def _capture_upsert(chunks, redis_client=None):
        captured_chunks.append(list(chunks))
        return len(chunks)

    with (
        patch("app.routers.articles.upsert_text_chunks", new=_capture_upsert),
        patch("app.routers.articles.analyze_article_text", new=AsyncMock(return_value=_FAKE_ANALYSIS)),
    ):
        response = client.post(
            "/v1/articles/analyze",
            headers={"X-Service-Token": "test-token"},
            json={
                "title": "Headaches",
                "text": article_body,
                "language": "en",
                "index_chunks": True,
            },
        )

    assert response.status_code == 200, response.text
    assert captured_chunks, "upsert_text_chunks was not called"
    chunks = captured_chunks[0]
    assert len(chunks) >= 3, f"expected multiple header-aware chunks, got {len(chunks)}"

    headers_seen = {c["metadata"].get("header") for c in chunks}
    # At least some chunks should carry a non-null header pulled from the article sections.
    non_null_headers = {h for h in headers_seen if h}
    assert non_null_headers, f"no chunks carried a header in metadata: {headers_seen}"
    # Each chunk's metadata keeps chunk_index / total_chunks alongside header.
    for chunk in chunks:
        assert "chunk_index" in chunk["metadata"]
        assert "total_chunks" in chunk["metadata"]
        assert "header" in chunk["metadata"]
