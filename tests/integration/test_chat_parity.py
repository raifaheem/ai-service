"""Parity check: /v1/chat and /v1/chat/stream produce the same answer + metadata."""
import json
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.services.intent import IntentResult


AUTH_HEADERS = {"X-Service-Token": "test-token", "X-User-Id": "parity-user"}
FULL_ANSWER = "Drink water and rest. Consult your doctor if symptoms persist."


async def _stream_generator():
    yield {"type": "delta", "text": FULL_ANSWER}
    yield {
        "type": "usage",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "model": "gpt-4o-mini",
        "finish_reason": "stop",
    }


def _iter_stream_events(body: bytes):
    for raw in body.decode("utf-8").split("\n\n"):
        if not raw.strip():
            continue
        event = None
        data = None
        for line in raw.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None:
            yield event, data


class TestChatParity:
    async def test_sync_and_stream_produce_same_answer(self, mock_redis, mock_qdrant):
        intent = IntentResult(
            category="symptom_check",
            confidence=0.9,
            requires_followup=False,
            detected_entities={},
            risk_level="medium",
        )
        body = {"message": "I have a headache", "locale": "en"}

        patches = (
            patch("app.services.redis_client._redis", mock_redis),
            patch("app.services.redis_client.get_redis", return_value=mock_redis),
            patch("app.services.vector_client._qdrant", mock_qdrant),
            patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
            patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
            patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=intent),
            patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("ctx", [], 0.42)),
            patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
            patch("app.routers.chat.generate_health_answer", new_callable=AsyncMock, return_value=FULL_ANSWER),
            patch("app.services.chat_stream.stream_health_answer", return_value=_stream_generator()),
        )
        for p in patches:
            p.start()
        try:
            from app.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                sync_resp = await client.post("/v1/chat", json=body, headers=AUTH_HEADERS)
                stream_resp = await client.post("/v1/chat/stream", json=body, headers=AUTH_HEADERS)
        finally:
            for p in patches:
                p.stop()

        assert sync_resp.status_code == 200
        assert stream_resp.status_code == 200

        sync = sync_resp.json()
        final_event = None
        for event_name, data in _iter_stream_events(stream_resp.content):
            if event_name == "final":
                final_event = data
        assert final_event is not None, "stream did not emit final event"

        # Core parity: same answer, same intent, same rag_used.
        # (disclaimer is appended by both, model/usage only populated for stream.)
        assert sync["answer"] == final_event["answer"]
        assert sync["disclaimer"] == final_event["disclaimer"]
        assert sync["intent"]["category"] == final_event["intent"]["category"]
        assert sync["intent"]["risk_level"] == final_event["intent"]["risk_level"]
        assert sync["rag_used"] == final_event["rag_used"]
