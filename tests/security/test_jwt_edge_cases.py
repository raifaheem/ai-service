"""Edge-case security coverage for the JWT + service-token auth guard."""

import datetime as dt
import os
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

# RS256 ephemeral keypair, generated once per process.
try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _PEM_PRIVATE = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    _PEM_PUBLIC = (
        _private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover — cryptography is in requirements
    _HAS_CRYPTO = False


def _make_token(claims: dict) -> str:
    return jwt.encode(claims, _PEM_PRIVATE, algorithm="RS256")


async def _chat(app, token: str | None = None, service_token: str | None = None) -> int:
    transport = ASGITransport(app=app)
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if service_token:
        headers["X-Service-Token"] = service_token
        headers["X-User-Id"] = "jwt-edge-user"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat", json={"message": "test"}, headers=headers)
    return resp.status_code


@pytest.fixture
def app_with_jwt(mock_redis, mock_qdrant):
    if not _HAS_CRYPTO:
        pytest.skip("cryptography package unavailable")
    original = os.environ.get("JWT_PUBLIC_KEY")
    os.environ["JWT_PUBLIC_KEY"] = _PEM_PUBLIC
    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
    ):
        # Reload config with the JWT key set
        from app import config as config_module

        config_module.settings.jwt_public_key = _PEM_PUBLIC
        from app.main import app

        yield app
    if original is None:
        os.environ.pop("JWT_PUBLIC_KEY", None)
    else:
        os.environ["JWT_PUBLIC_KEY"] = original


class TestJwtEdgeCases:
    async def test_expired_jwt_rejected(self, app_with_jwt):
        now = int(dt.datetime.now(dt.UTC).timestamp())
        token = _make_token({"sub": "user-1", "iat": now - 7200, "exp": now - 3600})
        assert await _chat(app_with_jwt, token=token) == 401

    async def test_jwt_missing_sub_rejected(self, app_with_jwt):
        now = int(dt.datetime.now(dt.UTC).timestamp())
        token = _make_token({"iat": now, "exp": now + 3600})
        assert await _chat(app_with_jwt, token=token) == 401

    async def test_jwt_missing_exp_rejected(self, app_with_jwt):
        now = int(dt.datetime.now(dt.UTC).timestamp())
        token = _make_token({"sub": "user-1", "iat": now})
        assert await _chat(app_with_jwt, token=token) == 401

    async def test_jwt_with_tampered_signature_rejected(self, app_with_jwt):
        now = int(dt.datetime.now(dt.UTC).timestamp())
        token = _make_token({"sub": "user-1", "iat": now, "exp": now + 3600})
        head, payload, _ = token.split(".")
        tampered = f"{head}.{payload}.AAAA"
        assert await _chat(app_with_jwt, token=tampered) == 401


class TestServiceTokenRotation:
    async def test_rotation_accepts_any_csv_token(self, mock_redis, mock_qdrant, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "old-token,new-token")
        from app import config as config_module

        config_module.settings.service_token = "old-token,new-token"

        with (
            patch("app.services.redis_client._redis", mock_redis),
            patch("app.services.redis_client.get_redis", return_value=mock_redis),
            patch("app.services.vector_client._qdrant", mock_qdrant),
            patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
            patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
            patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
        ):
            from app.main import app

            try:
                # Both tokens should get past the auth guard (either proceeds past 401)
                assert await _chat(app, service_token="old-token") != 401
                assert await _chat(app, service_token="new-token") != 401
                # Unknown token rejected
                assert await _chat(app, service_token="some-other") == 401
            finally:
                config_module.settings.service_token = "test-token"
