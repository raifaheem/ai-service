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
    """get returns None when nothing is cached; after set, get returns the entry."""
    from app.services import memory

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        assert await memory.get_idempotent_entry("user-1", "key-abc") is None
        await memory.set_idempotent_entry("user-1", "key-abc", "fp-1", {"answer": "cached"})
        entry = await memory.get_idempotent_entry("user-1", "key-abc")

    assert entry == {"fingerprint": "fp-1", "response": {"answer": "cached"}}


async def test_memory_idempotency_user_scoped(mock_redis):
    """Two users using the same key must not see each other's cached response."""
    from app.services import memory

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        await memory.set_idempotent_entry("user-a", "shared-key", "fp-a", {"answer": "for-A"})
        assert await memory.get_idempotent_entry("user-b", "shared-key") is None


async def test_memory_idempotency_rejects_legacy_format(mock_redis):
    """A pre-A5 cache entry without `fingerprint` must be treated as missing.

    Avoids returning a stale answer that we can't fingerprint-check.
    """
    import json

    from app.config import settings
    from app.services import memory

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        # Simulate a pre-A5 cache entry: bare response dict, no fingerprint wrapper.
        legacy_key = f"{settings.redis_prefix}:idem:user-l:legacy"
        await mock_redis.set(legacy_key, json.dumps({"answer": "old"}), ex=600)
        assert await memory.get_idempotent_entry("user-l", "legacy") is None


async def test_cache_hit_records_audit_event(mock_redis, mock_qdrant):
    """M1: a cache hit must still emit a `chat.answer` audit event so retried
    requests appear in forensics. The replay event is distinguished by
    `cached=true`."""
    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    audit_calls: list[dict] = []

    async def _capture_audit(event_type, **kwargs):
        audit_calls.append({"type": event_type, **kwargs})

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
            return_value="cached answer",
        ),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
        patch("app.routers.chat.record_audit_event", side_effect=_capture_audit),
    ):
        from app.main import app

        body = {"message": "hello", "locale": "en", "idempotency_key": "audit-rerun"}
        await _post_chat(app, body)
        await _post_chat(app, body)

    answer_events = [e for e in audit_calls if e["type"] == "chat.answer"]
    assert len(answer_events) == 2, f"expected 2 chat.answer events, got {len(answer_events)}: {audit_calls}"
    # First was the live pipeline (no `cached`), second was the cache replay (`cached=True`).
    assert not answer_events[0].get("cached")
    assert answer_events[1].get("cached") is True
    assert answer_events[1].get("idempotency_key") == "audit-rerun"


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


async def test_same_key_different_message_returns_409(mock_redis, mock_qdrant):
    """A5: reusing an idempotency_key with a different message body must return 409."""
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
            return_value="answer",
        ),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        await _post_chat(app, {"message": "first message", "locale": "en", "idempotency_key": "k-conflict"})

        # Second request: same key, different message → 409.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat",
                json={"message": "second message", "locale": "en", "idempotency_key": "k-conflict"},
                headers={"X-Service-Token": "test-token", "X-User-Id": "user-idem"},
            )
        assert resp.status_code == 409
        assert "idempotency_key" in resp.json()["detail"]


async def test_same_key_same_body_returns_cached(mock_redis, mock_qdrant):
    """A5: matching fingerprint still hits the cache (regression guard for the happy path)."""
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
        return "answer"

    body = {"message": "same body", "locale": "en", "region": "KZ", "idempotency_key": "k-match"}

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

        first = await _post_chat(app, body)
        second = await _post_chat(app, body)

    assert first["answer"] == second["answer"]
    assert generate_calls["n"] == 1


async def test_same_key_different_region_returns_409(mock_redis, mock_qdrant):
    """region is part of the fingerprint — switching it under a reused key must 409."""
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
            return_value="answer",
        ),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        await _post_chat(app, {"message": "msg", "locale": "en", "region": "KZ", "idempotency_key": "k-region"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat",
                json={"message": "msg", "locale": "en", "region": "US", "idempotency_key": "k-region"},
                headers={"X-Service-Token": "test-token", "X-User-Id": "user-idem"},
            )
        assert resp.status_code == 409
