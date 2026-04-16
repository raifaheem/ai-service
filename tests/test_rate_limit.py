from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.rate_limit import enforce_rate_limit, _key


class TestKeyFunction:
    def test_includes_identifier(self):
        key = _key("user-123")
        assert "user-123" in key

    def test_includes_rl_prefix(self):
        key = _key("user-123")
        assert ":rl:" in key

    def test_different_identifiers_different_keys(self):
        assert _key("user-a") != _key("user-b")


class TestEnforceRateLimit:
    async def test_allows_under_limit(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            await enforce_rate_limit("user-123")

    async def test_raises_429_over_limit(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[100, True])  # 100 > 25 (20+5)
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_rate_limit("user-123")
            assert exc_info.value.status_code == 429
            assert "Too many requests" in exc_info.value.detail

    async def test_allows_at_exact_limit(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[25, True])  # exactly at limit (20+5)
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            await enforce_rate_limit("user-123")

    async def test_raises_at_limit_plus_one(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[26, True])  # 26 > 25
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_rate_limit("user-123")
            assert exc_info.value.status_code == 429

    async def test_pipeline_called_correctly(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            await enforce_rate_limit("user-123")

        mock_redis.pipeline.assert_called_once_with(transaction=True)
        pipe.incr.assert_called_once()
        pipe.expire.assert_called_once()
