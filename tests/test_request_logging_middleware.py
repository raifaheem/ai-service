from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.middleware.request_logging import RequestLoggingMiddleware
from app.metrics import Metrics


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


@pytest.fixture
def fresh_metrics():
    return Metrics()


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


async def test_metrics_incremented_after_request(test_app, fresh_metrics):
    with patch("app.middleware.request_logging.metrics", fresh_metrics):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/test")
            await client.get("/test")
    assert fresh_metrics.requests_total == 2
    assert fresh_metrics.requests_by_status.get(200) == 2


async def test_error_metrics_recorded_on_exception(test_app, fresh_metrics):
    """When an endpoint raises, the middleware records the error in metrics before re-raising."""
    with patch("app.middleware.request_logging.metrics", fresh_metrics):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            try:
                await client.get("/error")
            except Exception:
                pass  # BaseHTTPMiddleware re-raises after our except block
    # The middleware's except block ran and recorded the 500 error
    assert fresh_metrics.requests_total == 1
    assert fresh_metrics.requests_by_status.get(500) == 1
