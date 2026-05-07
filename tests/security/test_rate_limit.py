"""End-to-end security smoke for the rate limiter.

M9 trim: this file used to patch `enforce_rate_limit` to throw 429 and assert
the route surfaced 429 — that proves FastAPI re-raises HTTPException, not that
the limiter works. The real sliding-window logic is unit-tested in
[tests/test_rate_limit.py](tests/test_rate_limit.py); here we keep one
end-to-end test that hits the real limiter through the chat router so a
regression in the integration wiring (rate_limit_id derivation, header parsing,
fail-closed behaviour) doesn't slip through.
"""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.config import settings


async def test_real_limiter_returns_429_after_budget(mock_redis, mock_qdrant):
    """Send `RATE_LIMIT_PER_MINUTE + RATE_LIMIT_BURST + 1` requests; the last is 429.

    The limiter writes to a Redis zset (sliding window); our `mock_redis`
    `_FakeRedis` already implements zadd/zcard/zremrangebyscore. We mock out
    the LLM and RAG paths so requests return 200 quickly until the budget runs
    out.
    """
    from app.services.intent import IntentResult

    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    cap = int(settings.rate_limit_per_minute) + int(settings.rate_limit_burst)

    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.rate_limit.get_redis", return_value=mock_redis),
        patch("app.services.memory.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent),
        patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
        patch(
            "app.routers.chat.generate_health_answer",
            new_callable=AsyncMock,
            return_value="ok",
        ),
    ):
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First `cap` requests succeed.
            for i in range(cap):
                resp = await client.post(
                    "/v1/chat",
                    json={"message": f"req-{i}", "locale": "en"},
                    headers={"X-Service-Token": "test-token", "X-User-Id": "rl-user"},
                )
                assert resp.status_code == 200, f"request {i} returned {resp.status_code}: {resp.text}"

            # The next one trips the limiter.
            resp = await client.post(
                "/v1/chat",
                json={"message": "over-the-edge", "locale": "en"},
                headers={"X-Service-Token": "test-token", "X-User-Id": "rl-user"},
            )
        assert resp.status_code == 429
