import pytest
from unittest.mock import patch
from fastapi import HTTPException

from app.security import auth_guard, resolve_user_id


class TestMultiTokenAuth:
    def test_single_token_valid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-abc"
            result = auth_guard(authorization=None, x_service_token="token-abc")
            assert result == {"auth": "service"}

    def test_single_token_invalid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-abc"
            with pytest.raises(HTTPException) as exc_info:
                auth_guard(authorization=None, x_service_token="wrong-token")
            assert exc_info.value.status_code == 401

    def test_multi_token_first_valid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a,token-b,token-c"
            result = auth_guard(authorization=None, x_service_token="token-a")
            assert result == {"auth": "service"}

    def test_multi_token_second_valid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a,token-b,token-c"
            result = auth_guard(authorization=None, x_service_token="token-b")
            assert result == {"auth": "service"}

    def test_multi_token_third_valid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a,token-b,token-c"
            result = auth_guard(authorization=None, x_service_token="token-c")
            assert result == {"auth": "service"}

    def test_multi_token_invalid(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a,token-b"
            with pytest.raises(HTTPException) as exc_info:
                auth_guard(authorization=None, x_service_token="token-x")
            assert exc_info.value.status_code == 401

    def test_multi_token_with_spaces(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a, token-b , token-c"
            result = auth_guard(authorization=None, x_service_token="token-b")
            assert result == {"auth": "service"}

    def test_no_auth_raises_401(self):
        with patch("app.security.settings") as mock_settings:
            mock_settings.service_token = "token-a"
            mock_settings.jwt_public_key = None
            with pytest.raises(HTTPException) as exc_info:
                auth_guard(authorization=None, x_service_token=None)
            assert exc_info.value.status_code == 401


class TestResolveUserId:
    def test_jwt_user(self):
        user_id = resolve_user_id({"auth": "jwt", "sub": "user-123"})
        assert user_id == "user-123"

    def test_service_with_header(self):
        user_id = resolve_user_id({"auth": "service"}, x_user_id="ext-456")
        assert user_id == "ext-456"

    def test_service_without_header_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_user_id({"auth": "service"})
        assert exc_info.value.status_code == 400

    def test_jwt_missing_sub_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_user_id({"auth": "jwt"})
        assert exc_info.value.status_code == 401
