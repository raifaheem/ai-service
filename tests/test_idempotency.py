"""Tests for idempotency-key handling on /v1/chat (C.4).

Guarantee: two requests from the same user with the same idempotency_key within
the 10-minute window share a response, and the second one does NOT hit OpenAI.
"""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.services.intent import IntentResult


async def _post_chat(app, body: dict) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat",
            json=body,
            headers={"X-Service-Token": "test-token", "X-User-Id": "user-idem"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_memory_idempotency_roundtrip(mock_redis):
    """get returns None when nothing is cached; after set, get returns the cached body."""
    from app.services import memory

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        assert await memory.get_idempotent_response("user-1", "key-abc") is None
        await memory.set_idempotent_response("user-1", "key-abc", {"answer": "cached"})
        result = await memory.get_idempotent_response("user-1", "key-abc")

    assert result == {"answer": "cached"}


async def test_memory_idempotency_user_scoped(mock_redis):
    """Two users using the same key must not see each other's cached response."""
    from app.services import memory

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        await memory.set_idempotent_response("user-a", "shared-key", {"answer": "for-A"})
        assert await memory.get_idempotent_response("user-b", "shared-key") is None


async def test_second_request_with_same_key_returns_cached_and_skips_openai(mock_redis, mock_qdrant):
    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    generate_calls = {"n": 0}

    async def _mock_generate(*args, **kwargs):
        generate_calls["n"] += 1
        return "Drink water and rest."

    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.memory.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent),
        patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
        patch("app.routers.chat.generate_health_answer", side_effect=_mock_generate),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        body = {"message": "hello", "locale": "en", "idempotency_key": "req-xyz-1"}
        first = await _post_chat(app, body)
        second = await _post_chat(app, body)

    assert first["answer"] == second["answer"]
    assert generate_calls["n"] == 1, f"OpenAI called {generate_calls['n']} times, expected 1"


async def test_different_idempotency_keys_both_hit_openai(mock_redis, mock_qdrant):
    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    generate_calls = {"n": 0}

    async def _mock_generate(*args, **kwargs):
        generate_calls["n"] += 1
        return f"answer-{generate_calls['n']}"

    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.memory.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent),
        patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
        patch("app.routers.chat.generate_health_answer", side_effect=_mock_generate),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        await _post_chat(app, {"message": "hello", "locale": "en", "idempotency_key": "k-1"})
        await _post_chat(app, {"message": "hello", "locale": "en", "idempotency_key": "k-2"})

    assert generate_calls["n"] == 2


async def test_no_idempotency_key_never_caches(mock_redis, mock_qdrant):
    """A request without idempotency_key must not leave a cache entry behind."""
    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.memory.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent),
        patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
        patch(
            "app.routers.chat.generate_health_answer",
            new_callable=AsyncMock,
            return_value="plain",
        ),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        await _post_chat(app, {"message": "hello", "locale": "en"})

    keys = await mock_redis.keys("*idem*")
    assert keys == []
