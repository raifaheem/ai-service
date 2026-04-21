import contextlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.metrics import REQUESTS_TOTAL
from app.middleware.request_logging import RequestLoggingMiddleware, _path_tag


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the middleware for testing."""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    @app.get("/health")
    async def health_endpoint():
        return {"status": "ok"}

    @app.get("/error")
    async def error_endpoint():
        raise ValueError("test error")

    return app


@pytest.fixture
def test_app():
    return _create_test_app()


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


async def test_response_includes_x_request_id(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


async def test_provided_request_id_is_passed_through(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test", headers={"X-Request-Id": "my-custom-id"})
    assert resp.headers["x-request-id"] == "my-custom-id"


async def test_normal_endpoint_still_works(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_health_endpoint_still_works(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


async def test_prometheus_counter_increments_for_2xx(test_app):
    before = _counter_value(REQUESTS_TOTAL, status="200", path_tag="other")
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/test")
        await client.get("/test")
    # /test is not in _STATIC_PATHS — it collapses to "other".
    assert _counter_value(REQUESTS_TOTAL, status="200", path_tag="other") == before + 2


async def test_prometheus_counter_increments_for_5xx_on_exception(test_app):
    before = _counter_value(REQUESTS_TOTAL, status="500", path_tag="other")
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with contextlib.suppress(Exception):
            await client.get("/error")
    assert _counter_value(REQUESTS_TOTAL, status="500", path_tag="other") == before + 1


class TestPathTag:
    def test_static_path_passes_through(self):
        assert _path_tag("/health") == "/health"
        assert _path_tag("/metrics") == "/metrics"
        assert _path_tag("/v1/chat") == "/v1/chat"
        assert _path_tag("/v1/chat/stream") == "/v1/chat/stream"

    def test_conversation_id_collapses(self):
        assert _path_tag("/v1/conversations/abc-123") == "/v1/conversations/:id"
        assert _path_tag("/v1/conversations/another-id") == "/v1/conversations/:id"

    def test_unknown_path_becomes_other(self):
        assert _path_tag("/random") == "other"
