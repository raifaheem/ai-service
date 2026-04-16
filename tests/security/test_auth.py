"""Security tests: authentication and authorization."""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


class TestAuthEndpoints:
    async def test_no_auth_chat(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/v1/chat", json={"message": "Hello"})
        assert resp.status_code == 401

    async def test_invalid_token_chat(self, mock_redis, mock_qdrant):
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
                    json={"message": "Hello"},
                    headers={"X-Service-Token": "wrong-token"},
                )
        assert resp.status_code == 401

    async def test_service_auth_without_user_id(self, mock_redis, mock_qdrant):
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
                    json={"message": "Hello"},
                    headers={"X-Service-Token": "test-token"},  # no X-User-Id
                )
        assert resp.status_code == 400

    async def test_invalid_bearer_token(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.security.settings") as mock_settings:

            mock_settings.service_token = "test-token"
            mock_settings.jwt_public_key = "fake-key"
            mock_settings.jwt_alg = "HS256"

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "Hello"},
                    headers={"Authorization": "Bearer invalid.jwt.token"},
                )
        assert resp.status_code == 401

    async def test_no_auth_conversations(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/conversations/some-id")
        assert resp.status_code == 401

    async def test_no_auth_stream(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/stream",
                    json={"message": "Hello"},
                )
        assert resp.status_code == 401


class TestConversationOwnership:
    async def test_cannot_access_other_users_conversation(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=b"other-user")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/v1/conversations/conv-123",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "attacker"},
                )
        assert resp.status_code == 403

    async def test_cannot_delete_other_users_conversation(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=b"legitimate-owner")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    "/v1/conversations/conv-123",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "attacker"},
                )
        assert resp.status_code == 403
