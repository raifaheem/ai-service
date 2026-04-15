from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "checks" in data
    assert "version" in data