"""Verify the request-logging middleware never leaks sensitive headers."""
import logging
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient


SENSITIVE_FRAGMENTS = [
    "super-secret-jwt",
    "Bearer",
    "top-secret-service-token",
]


async def _trigger_request(mock_redis, mock_qdrant, headers: dict):
    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):
        from app.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/health", headers=headers)


async def test_authorization_header_not_in_logs(caplog, mock_redis, mock_qdrant):
    caplog.set_level(logging.DEBUG, logger="app.middleware.request_logging")
    await _trigger_request(
        mock_redis,
        mock_qdrant,
        headers={"Authorization": "Bearer super-secret-jwt"},
    )
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    extras = "\n".join(str(getattr(record, "__dict__", {})) for record in caplog.records)
    for fragment in ["super-secret-jwt", "Bearer super-secret-jwt"]:
        assert fragment not in log_text
        assert fragment not in extras


async def test_service_token_header_not_in_logs(caplog, mock_redis, mock_qdrant):
    caplog.set_level(logging.DEBUG, logger="app.middleware.request_logging")
    await _trigger_request(
        mock_redis,
        mock_qdrant,
        headers={"X-Service-Token": "top-secret-service-token"},
    )
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    extras = "\n".join(str(getattr(record, "__dict__", {})) for record in caplog.records)
    assert "top-secret-service-token" not in log_text
    assert "top-secret-service-token" not in extras
