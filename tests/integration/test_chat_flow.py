"""Integration tests: full chat request flow with mocked external services."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.services.intent import IntentResult


class TestFullChatFlow:
    """Test the complete request lifecycle: auth → rate limit → intent → RAG → LLM → memory → response."""

    async def test_full_non_streaming_flow(self, mock_redis, mock_qdrant):
        mock_intent = IntentResult(category="symptom_check", confidence=0.95, requires_followup=False, detected_entities={}, risk_level="medium")

        # RAG returns some chunks
        rag_chunks = [
            {"text": "Headache treatment info", "source_id": "src-1", "title": "Headache Guide", "score": 0.88, "language": "en"},
        ]

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent), \
             patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("RAG context here", rag_chunks, 0.88)), \
             patch("app.routers.chat.generate_health_answer", new_callable=AsyncMock, return_value="Rest and stay hydrated for headaches."), \
             patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "I have a headache, what should I do?",
                        "locale": "en",
                        "profile": {"age": 35, "sex": "female"},
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )

        assert resp.status_code == 200
        data = resp.json()

        # Response structure
        assert "answer" in data
        assert "disclaimer" in data
        assert "conversation_id" in data
        assert data["rag_used"] is True
        assert data["rag_score"] == 0.88
        assert data["intent"]["category"] == "symptom_check"
        assert data["intent"]["risk_level"] == "medium"

        # Sources present
        assert data["sources"] is not None
        assert len(data["sources"]) == 1
        assert data["sources"][0]["source_id"] == "src-1"

    async def test_full_flow_with_history(self, mock_redis, mock_qdrant):
        mock_intent = IntentResult(category="general_health", confidence=0.85, requires_followup=False, detected_entities={}, risk_level="low")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent), \
             patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)), \
             patch("app.routers.chat.generate_health_answer", new_callable=AsyncMock, return_value="Follow-up answer."), \
             patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={
                        "message": "And what about sleep?",
                        "locale": "en",
                        "history": [
                            {"role": "user", "content": "How to be healthy?"},
                            {"role": "assistant", "content": "Eat well and exercise."},
                        ],
                    },
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"].startswith("Follow-up answer")

    async def test_rag_failure_graceful(self, mock_redis, mock_qdrant):
        """If RAG fails, the flow should continue without RAG context."""
        mock_intent = IntentResult(category="general_health", confidence=0.85, requires_followup=False, detected_entities={}, risk_level="low")

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent), \
             patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, side_effect=Exception("Qdrant down")), \
             patch("app.routers.chat.generate_health_answer", new_callable=AsyncMock, return_value="General health advice."), \
             patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "General health question", "locale": "en"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["rag_used"] is False


class TestStreamingFlow:
    async def test_full_streaming_flow(self, mock_redis, mock_qdrant):
        mock_intent = IntentResult(category="general_health", confidence=0.85, requires_followup=False, detected_entities={}, risk_level="low")

        async def mock_stream(*args, **kwargs):
            yield {"type": "delta", "text": "Streaming "}
            yield {"type": "delta", "text": "response."}
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                "model": "gpt-4o-mini",
                "finish_reason": "stop",
            }

        with patch("app.services.redis_client._redis", mock_redis), \
             patch("app.services.redis_client.get_redis", return_value=mock_redis), \
             patch("app.services.vector_client._qdrant", mock_qdrant), \
             patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
             patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock), \
             patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent), \
             patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)), \
             patch("app.routers.chat.stream_health_answer", side_effect=mock_stream), \
             patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock):

            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/stream",
                    json={"message": "Tell me about nutrition", "locale": "en"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "user-42"},
                )

        assert resp.status_code == 200
        body = resp.text
        assert "event: meta" in body
        assert "event: delta" in body
        assert "event: final" in body
        assert "Streaming " in body
        assert "response." in body
