"""Integration tests for post-LLM sensitive-topic screening.

The pre-LLM gate (`detect_sensitive_topic` on the user message) doesn't help
when the model paraphrases a banned word out of parametric knowledge or a
stale KB chunk. May 2026 regression: a sleep-hygiene question got the textbook
"bed for sleep and sex" answer back. This module pins the post-LLM screen on
both the sync and streaming paths.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.content_safety import SENSITIVE_REFUSAL
from app.services.intent import IntentResult


_LEAKED_RU = (
    "Используйте кровать только для сна (и секса) — не работайте и "
    "не смотрите телевизор в постели."
)


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into ``[(event_name, payload), ...]``."""
    events: list[tuple[str, dict]] = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        ev = ""
        data = ""
        for line in raw.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if ev and data:
            events.append((ev, json.loads(data)))
    return events


@pytest.fixture
def sleep_intent():
    return IntentResult(
        category="sleep",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )


class TestSyncPostScreen:
    async def test_sync_replaces_leaked_answer_with_refusal(
        self, mock_redis, mock_qdrant, sleep_intent
    ):
        with (
            patch("app.services.redis_client._redis", mock_redis),
            patch("app.services.redis_client.get_redis", return_value=mock_redis),
            patch("app.services.vector_client._qdrant", mock_qdrant),
            patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
            patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
            patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=sleep_intent),
            patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
            patch(
                "app.routers.chat.generate_health_answer",
                new_callable=AsyncMock,
                return_value=_LEAKED_RU,
            ),
            patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
        ):
            from app.main import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat",
                    json={"message": "Помоги с режимом сна", "locale": "ru"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "screen-sync-1"},
                )

        assert resp.status_code == 200
        data = resp.json()
        # The LLM-leaked answer must NOT be returned to the user.
        assert "секс" not in data["answer"].lower()
        # The canonical refusal IS what the user sees.
        assert data["answer"] == SENSITIVE_REFUSAL["ru"]
        # rag_used flips false on a post-screen block — there's no RAG in a
        # refusal message, even if the original answer used RAG.
        assert data["rag_used"] is False


class TestStreamPostScreen:
    async def test_stream_final_swaps_to_refusal_on_leak(
        self, mock_redis, mock_qdrant, sleep_intent
    ):
        async def mock_stream(*args, **kwargs):
            # Split the leaked phrase across two deltas to emulate real streaming.
            yield {"type": "delta", "text": _LEAKED_RU[:40]}
            yield {"type": "delta", "text": _LEAKED_RU[40:]}
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                "model": "gpt-4o-mini",
                "finish_reason": "stop",
            }

        with (
            patch("app.services.redis_client._redis", mock_redis),
            patch("app.services.redis_client.get_redis", return_value=mock_redis),
            patch("app.services.vector_client._qdrant", mock_qdrant),
            patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
            patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
            patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=sleep_intent),
            patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
            patch("app.services.chat_stream.stream_health_answer", side_effect=mock_stream),
            patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
        ):
            from app.main import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/stream",
                    json={"message": "Помоги с режимом сна", "locale": "ru"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "screen-stream-1"},
                )

        assert resp.status_code == 200
        events = _sse_events(resp.text)
        finals = [data for ev, data in events if ev == "final"]
        assert len(finals) == 1
        final = finals[0]
        # `final.answer` must be the refusal, not the leaked content. The dev-UI
        # contract ("if final.answer != accumulated_text, replace bubble") relies
        # on this.
        assert final["answer"] == SENSITIVE_REFUSAL["ru"]
        assert final["finish_reason"] == "sensitive_blocked_post"
        # No leak in the final payload itself.
        assert "секс" not in final["answer"].lower()
