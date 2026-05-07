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
        patch("app.routers.articles.enforce_rate_limit", new=AsyncMock()),
    ):
        response = client.post(
            "/v1/articles/analyze-file",
            headers={"X-Service-Token": "test-token", "X-User-Id": "articles-test-user"},
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
        patch("app.routers.articles.enforce_rate_limit", new=AsyncMock()),
    ):
        response = client.post(
            "/v1/articles/analyze",
            headers={"X-Service-Token": "test-token", "X-User-Id": "articles-test-user"},
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


# --------------- S6: rate-limit + max_length ---------------


def test_analyze_without_x_user_id_is_400():
    """S6: service-token auth without X-User-Id can't be rate-limited per user, so reject."""
    client = TestClient(app)
    response = client.post(
        "/v1/articles/analyze",
        headers={"X-Service-Token": "test-token"},  # no X-User-Id
        json={
            "title": "Headache",
            "text": "headache " * 50,
            "language": "en",
            "index_chunks": False,
        },
    )
    assert response.status_code == 400
    assert "X-User-Id" in response.json()["detail"]


def test_analyze_rate_limit_enforced():
    """S6: when the limiter raises 429, the route surfaces it (no LLM call)."""
    from fastapi import HTTPException

    client = TestClient(app)
    llm_calls = {"n": 0}

    async def _fake_llm(*args, **kwargs):
        llm_calls["n"] += 1
        return _FAKE_ANALYSIS

    async def _raise_429(_):
        raise HTTPException(status_code=429, detail="Too many requests")

    with (
        patch("app.routers.articles.enforce_rate_limit", new=_raise_429),
        patch("app.routers.articles.analyze_article_text", new=_fake_llm),
        patch("app.routers.articles.upsert_text_chunks", new=AsyncMock(return_value=1)),
    ):
        response = client.post(
            "/v1/articles/analyze",
            headers={"X-Service-Token": "test-token", "X-User-Id": "rl-user"},
            json={
                "title": "Headache",
                "text": "headache " * 50,
                "language": "en",
                "index_chunks": False,
            },
        )

    assert response.status_code == 429
    assert llm_calls["n"] == 0


def test_analyze_text_max_length_rejected():
    """S6: 200_001-char body rejected before the route runs."""
    client = TestClient(app)
    response = client.post(
        "/v1/articles/analyze",
        headers={"X-Service-Token": "test-token", "X-User-Id": "len-user"},
        json={
            "title": "Long",
            "text": "x" * 200_001,
            "language": "en",
            "index_chunks": False,
        },
    )
    assert response.status_code == 422
