"""Security tests: rate limiting behavior."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


class TestRateLimiting:
    async def test_rate_limit_exceeded(self, mock_redis, mock_qdrant):
        """When rate limit is exceeded, should return 429."""
        # Make enforce_rate_limit raise 429
        from fastapi import HTTPException

        async def rate_limit_exceeded(identifier):
            raise HTTPException(status_code=429, detail="Too many requests")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.enforce_rate_limit", side_effect=rate_limit_exceeded):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "Hello"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 429

    async def test_rate_limit_exceeded_stream(self, mock_redis, mock_qdrant):
        """Rate limit on streaming endpoint too."""
        from fastapi import HTTPException

        async def rate_limit_exceeded(identifier):
            raise HTTPException(status_code=429, detail="Too many requests")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.enforce_rate_limit", side_effect=rate_limit_exceeded):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/stream",
                    json={"message": "Hello"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-1"},
                )
        assert resp.status_code == 429
