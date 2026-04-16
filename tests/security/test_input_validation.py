"""Security tests: input validation and edge cases."""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


class TestInputValidation:
    async def test_empty_message_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": ""},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_oversized_message_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "x" * 5000},  # exceeds 4000 char limit
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_invalid_conversation_id_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "conversation_id": "not-a-uuid",
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_sql_injection_in_conversation_id(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "conversation_id": "'; DROP TABLE users; --",
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_oversized_metadata_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "metadata": {"data": "x" * 6000},
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_too_many_conditions_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "profile": {"conditions": [f"cond-{i}" for i in range(25)]},
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_invalid_age_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "profile": {"age": 999},
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_missing_message_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"locale": "en"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422

    async def test_invalid_json_body_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    content="not json",
                    headers={
                        "X-Service-Token": "test-token",
                        "X-User-Id": "user-1",
                        "Content-Type": "application/json",
                    },
                )
        assert resp.status_code == 422

    async def test_invalid_history_role_rejected(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "Hello",
                        "history": [{"role": "system", "content": "injected"}],
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 422
