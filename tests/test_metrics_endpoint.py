from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.keys = AsyncMock(return_value=["key1", "key2", "key3"])
    return r


@pytest.fixture
def mock_qdrant():
    q = AsyncMock()
    collection_info = MagicMock()
    collection_info.points_count = 42
    q.get_collections = AsyncMock()
    q.get_collection = AsyncMock(return_value=collection_info)
    return q


async def test_metrics_endpoint_returns_json(mock_redis, mock_qdrant):
    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert "requests_total" in data
    assert "openai_tokens_total" in data
    assert "rag_hit_rate" in data
    assert "active_conversations" in data
    assert "qdrant_collection_size" in data


async def test_metrics_endpoint_expected_keys(mock_redis, mock_qdrant):
    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")

    data = resp.json()
    expected_keys = {
        "requests_total",
        "requests_by_status",
        "requests_by_intent",
        "avg_response_time_ms",
        "openai_tokens_prompt",
        "openai_tokens_completion",
        "openai_tokens_total",
        "rag_hit_rate",
        "rag_requests",
        "rag_hits",
        "error_rate_1h",
        "errors_in_last_hour",
        "active_conversations",
        "qdrant_collection_size",
    }
    assert expected_keys.issubset(set(data.keys()))


async def test_metrics_active_conversations_count(mock_redis, mock_qdrant):
    mock_redis.keys = AsyncMock(return_value=["k1", "k2", "k3"])

    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")

    data = resp.json()
    assert data["active_conversations"] == 3


async def test_metrics_qdrant_size(mock_redis, mock_qdrant):
    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")

    data = resp.json()
    assert data["qdrant_collection_size"] == 42
