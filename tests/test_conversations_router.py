import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.services.memory import Turn


class TestGetConversation:
    async def test_get_conversation_success(self, mock_redis, mock_qdrant):
        turns_data = [
            json.dumps({"role": "user", "content": "Hello", "ts": 1000.0}),
            json.dumps({"role": "assistant", "content": "Hi there", "ts": 1001.0}),
        ]
        mock_redis.lrange = AsyncMock(return_value=turns_data)
        mock_redis.get = AsyncMock(return_value=b"user-1")
        mock_redis.ttl = AsyncMock(return_value=3600)

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
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == "conv-123"
        assert len(data["turns"]) == 2
        assert data["turns"][0]["role"] == "user"

    async def test_get_conversation_not_found(self, mock_redis, mock_qdrant):
        mock_redis.lrange = AsyncMock(return_value=[])
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.ttl = AsyncMock(return_value=-2)

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/v1/conversations/nonexistent",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 404

    async def test_get_conversation_access_denied(self, mock_redis, mock_qdrant):
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
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 403

    async def test_get_conversation_no_auth(self, mock_redis, mock_qdrant):
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/conversations/conv-123")

        assert resp.status_code == 401


class TestGetConversationMetadata:
    async def test_get_metadata_success(self, mock_redis, mock_qdrant):
        meta = {"topic": "symptom_check", "turn_count": 4}
        # get_owner returns user-1, get_metadata returns meta
        mock_redis.get = AsyncMock(side_effect=[b"user-1", json.dumps(meta)])
        mock_redis.ttl = AsyncMock(return_value=3000)

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/v1/conversations/conv-123/metadata",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] == "symptom_check"
        assert data["turn_count"] == 4

    async def test_get_metadata_not_found(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=None)

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/v1/conversations/conv-123/metadata",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 404


class TestDeleteConversation:
    async def test_delete_success(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=b"user-1")
        mock_redis.delete = AsyncMock(return_value=4)

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
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    async def test_delete_not_found(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.delete = AsyncMock(return_value=0)

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
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 404

    async def test_delete_access_denied(self, mock_redis, mock_qdrant):
        mock_redis.get = AsyncMock(return_value=b"different-user")

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
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )

        assert resp.status_code == 403
