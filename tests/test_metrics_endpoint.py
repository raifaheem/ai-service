"""Tests for the /metrics HTTP endpoint (B.2).

Endpoint now returns Prometheus text format instead of JSON.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.keys = AsyncMock(return_value=["key1", "key2", "key3"])
    # Circuit breaker reads state via hgetall on /metrics; if it raises, the
    # /metrics handler swallows the error and never sets the gauge, which makes
    # the `healthai_circuit_breaker_state` series invisible to the scrape.
    r.hgetall = AsyncMock(return_value={})
    r.hset = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    return r


@pytest.fixture
def mock_qdrant():
    q = AsyncMock()
    collection_info = MagicMock()
    collection_info.points_count = 42
    q.get_collections = AsyncMock()
    q.get_collection = AsyncMock(return_value=collection_info)
    return q


def _patched(mock_redis, mock_qdrant):
    return [
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
    ]


async def _get_metrics(mock_redis, mock_qdrant, headers=None):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patched(mock_redis, mock_qdrant):
            stack.enter_context(p)

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/metrics", headers=headers or {})


async def test_metrics_endpoint_returns_prometheus_text(mock_redis, mock_qdrant):
    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]


async def test_metrics_endpoint_exposes_core_metric_names(mock_redis, mock_qdrant):
    from app.metrics import metrics

    # Seed some data so the collectors aren't empty on first scrape.
    metrics.record_request(200, 12.3, path_tag="/health")
    metrics.record_intent("general_health", risk_level="low")
    metrics.record_openai_usage(100, 50, call_type="generate")
    metrics.record_rag_result(True)

    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    body = resp.text
    assert "healthai_requests_total" in body
    assert "healthai_request_duration_seconds" in body
    assert "healthai_intent_total" in body
    assert "healthai_openai_tokens_total" in body
    assert "healthai_rag_requests_total" in body
    assert "healthai_circuit_breaker_state" in body


async def test_metrics_endpoint_reports_circuit_breaker_state(mock_redis, mock_qdrant):
    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    body = resp.text
    assert 'healthai_circuit_breaker_state{name="openai"}' in body
    assert 'healthai_circuit_breaker_state{name="qdrant"}' in body


async def test_metrics_endpoint_reports_active_conversations_gauge(mock_redis, mock_qdrant):
    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    body = resp.text
    assert "healthai_active_conversations" in body


async def test_metrics_endpoint_reports_qdrant_collection_size(mock_redis, mock_qdrant):
    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    body = resp.text
    assert "healthai_qdrant_collection_size" in body


async def test_metrics_requires_auth(mock_redis, mock_qdrant):
    resp = await _get_metrics(mock_redis, mock_qdrant, headers=None)
    assert resp.status_code == 401


# --- METRICS_SCRAPE_TOKEN Bearer fallback (for Prometheus, which can't send
# the X-Service-Token custom header in scrape_configs). ----------------------


async def test_metrics_accepts_scrape_token_via_bearer(monkeypatch, mock_redis, mock_qdrant):
    # Override the runtime-loaded settings.metrics_scrape_token; tests freeze
    # the module-level singleton at import time so monkeypatching the attr
    # directly is the supported pattern (see tests/test_config.py).
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "metrics_scrape_token", "prom-scrape-secret-32hex")

    resp = await _get_metrics(
        mock_redis, mock_qdrant, {"Authorization": "Bearer prom-scrape-secret-32hex"}
    )
    assert resp.status_code == 200
    assert "healthai_requests_total" in resp.text


async def test_metrics_rejects_wrong_bearer_when_scrape_token_set(
    monkeypatch, mock_redis, mock_qdrant
):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "metrics_scrape_token", "the-real-token")

    resp = await _get_metrics(
        mock_redis, mock_qdrant, {"Authorization": "Bearer not-the-token"}
    )
    # No JWT_PUBLIC_KEY configured, no X-Service-Token sent → auth_guard
    # rejects with 401 (the "JWT auth not configured" path).
    assert resp.status_code == 401


async def test_metrics_scrape_token_unset_falls_back_to_x_service_token(mock_redis, mock_qdrant):
    # With metrics_scrape_token=None (default in tests), Bearer auth must NOT
    # short-circuit and X-Service-Token must still work.
    resp = await _get_metrics(mock_redis, mock_qdrant, {"X-Service-Token": "test-token"})
    assert resp.status_code == 200
