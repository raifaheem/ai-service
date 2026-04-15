from io import BytesIO
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app


def test_analyze_file_txt():
    client = TestClient(app)

    fake_analysis = {
        "summary": "Test summary",
        "key_findings": ["Finding 1", "Finding 2"],
        "limitations": ["Limitation 1"],
        "practical_meaning": ["Meaning 1"],
        "red_flags": ["Red flag 1"],
        "confidence": "medium",
    }

    with patch("app.routers.articles.upsert_text_chunks", new=AsyncMock(return_value=1)), \
         patch("app.routers.articles.analyze_article_text", new=AsyncMock(return_value=fake_analysis)):

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