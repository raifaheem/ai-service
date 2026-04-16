"""Integration tests: conversation create → get → metadata → delete lifecycle."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.services.intent import IntentResult


class TestConversationLifecycle:
    async def test_create_then_get_conversation(self, mock_redis, mock_qdrant):
        """Create a conversation via chat, then retrieve it."""
        mock_intent = IntentResult(category="general_health", confidence=0.85, requires_followup=False, detected_entities={}, risk_level="low")

        # After chat, get_history returns the turns that were appended
        turns_after_chat = [
            json.dumps({"role": "user", "content": "Hello", "ts": 1000.0}),
            json.dumps({"role": "assistant", "content": "Hi there", "ts": 1001.0}),
        ]

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent), \
             patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)), \
             patch("app.routers.chat.generate_health_answer", new_callable=AsyncMock, return_value="Hello! How can I help?"), \
             patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Step 1: Create conversation
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "Hello", "locale": "en"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )
                assert resp.status_code == 200
                conv_id = resp.json()["conversation_id"]

                # Step 2: Retrieve conversation
                mock_redis.lrange = AsyncMock(return_value=turns_after_chat)
                mock_redis.get = AsyncMock(return_value=b"user-42")
                mock_redis.ttl = AsyncMock(return_value=3600)

                resp2 = await client.get(
                    f"/v1/conversations/{conv_id}",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )

        assert resp2.status_code == 200
        data = resp2.json()
        assert data["conversation_id"] == conv_id
        assert len(data["turns"]) == 2

    async def test_delete_then_not_found(self, mock_redis, mock_qdrant):
        """Delete a conversation, then verify it returns 404."""
        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Step 1: Delete
                mock_redis.get = AsyncMock(return_value=b"user-42")
                mock_redis.delete = AsyncMock(return_value=4)

                resp = await client.delete(
                    "/v1/conversations/conv-to-delete",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )
                assert resp.status_code == 200
                assert resp.json()["deleted"] is True

                # Step 2: Try to get the deleted conversation
                mock_redis.lrange = AsyncMock(return_value=[])
                mock_redis.get = AsyncMock(return_value=None)

                resp2 = await client.get(
                    "/v1/conversations/conv-to-delete",
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )
                assert resp2.status_code == 404
