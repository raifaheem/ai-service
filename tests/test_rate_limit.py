"""Tests for sliding-window rate limiting (A.4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.rate_limit import _key, enforce_rate_limit


class TestKeyFunction:
    def test_includes_identifier(self):
        assert "user-123" in _key("user-123")

    def test_includes_rl_prefix(self):
        assert ":rl:" in _key("user-123")

    def test_different_identifiers_different_keys(self):
        assert _key("user-a") != _key("user-b")

    def test_key_does_not_include_minute_floor(self):
        """Sliding window keys must not carry a per-minute suffix."""
        assert _key("user-123").endswith("user-123")


def _mock_redis_with_zcard(zcard_result: int):
    """Build a mock async Redis whose pipeline returns `zcard_result` at index 2."""
    pipe = AsyncMock()
    pipe.zremrangebyscore = MagicMock()
    pipe.zadd = MagicMock()
    pipe.zcard = MagicMock()
    pipe.expire = MagicMock()
    # Pipeline order: zremrangebyscore, zadd, zcard, expire  →  results index 2 = zcard.
    pipe.execute = AsyncMock(return_value=[0, 1, zcard_result, True])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)

    mock = AsyncMock()
    mock.pipeline = MagicMock(return_value=pipe)
    return mock, pipe


class TestEnforceRateLimit:
    async def test_allows_under_limit(self):
        mock, _ = _mock_redis_with_zcard(1)
        with patch("app.services.rate_limit.get_redis", return_value=mock):
            await enforce_rate_limit("user-123")

    async def test_allows_at_exact_limit(self):
        # Default settings: rate_limit_per_minute=20, burst=5 → limit 25.
        mock, _ = _mock_redis_with_zcard(25)
        with patch("app.services.rate_limit.get_redis", return_value=mock):
            await enforce_rate_limit("user-123")

    async def test_raises_at_limit_plus_one(self):
        mock, _ = _mock_redis_with_zcard(26)
        with patch("app.services.rate_limit.get_redis", return_value=mock):
            with pytest.raises(HTTPException) as exc:
                await enforce_rate_limit("user-123")
            assert exc.value.status_code == 429

    async def test_pipeline_runs_expected_zset_commands(self):
        mock, pipe = _mock_redis_with_zcard(1)
        with patch("app.services.rate_limit.get_redis", return_value=mock):
            await enforce_rate_limit("user-123")

        mock.pipeline.assert_called_once_with(transaction=True)
        pipe.zremrangebyscore.assert_called_once()
        pipe.zadd.assert_called_once()
        pipe.zcard.assert_called_once()
        pipe.expire.assert_called_once()


class TestSlidingWindowBoundary:
    """Burst across minute boundaries must still be caught.

    Uses the stateful _FakeRedis from conftest plus a patched time.time to
    replay requests at specific timestamps.
    """

    async def test_burst_across_minute_boundary_is_caught(self, mock_redis):
        """Floor-minute limiter rolled at :00; sliding window must not.

        Fires 3 requests at :30 then a 4th at :60 (next minute). A floor-minute
        key would reset the bucket and let the 4th through; the sliding window
        still has all three within the last 60s, so the 4th must be rejected.
        """
        from app import config as config_module

        config_module.settings.rate_limit_per_minute = 3
        config_module.settings.rate_limit_burst = 0

        try:
            t = [1_000_000_000.0]

            def fake_time():
                return t[0]

            with (
                patch("app.services.rate_limit.get_redis", return_value=mock_redis),
                patch("app.services.rate_limit.time.time", side_effect=fake_time),
            ):
                t[0] = 1_000_000_030.0
                await enforce_rate_limit("user-boundary")
                t[0] = 1_000_000_045.0
                await enforce_rate_limit("user-boundary")
                t[0] = 1_000_000_058.0
                await enforce_rate_limit("user-boundary")

                # Next minute — floor-minute key would have rolled over, but sliding
                # window still sees all three prior hits (within last 60s).
                t[0] = 1_000_000_060.0
                with pytest.raises(HTTPException) as exc:
                    await enforce_rate_limit("user-boundary")
                assert exc.value.status_code == 429

                # Jump past 60s from the last accepted hit (:58) → window drains.
                t[0] = 1_000_000_200.0
                await enforce_rate_limit("user-boundary")  # must NOT raise
        finally:
            config_module.settings.rate_limit_per_minute = 20
            config_module.settings.rate_limit_burst = 5

    async def test_parallel_same_millisecond_requests_counted_separately(self, mock_redis):
        """Two requests arriving at the exact same ms should both count (unique members)."""
        from app import config as config_module

        config_module.settings.rate_limit_per_minute = 5
        config_module.settings.rate_limit_burst = 0

        with (
            patch("app.services.rate_limit.get_redis", return_value=mock_redis),
            patch("app.services.rate_limit.time.time", return_value=2_000_000_000.0),
        ):
            for _ in range(5):
                await enforce_rate_limit("user-parallel")  # 5 hits at the same ms
            with pytest.raises(HTTPException):
                await enforce_rate_limit("user-parallel")  # 6th same ms → 429

        config_module.settings.rate_limit_per_minute = 20
        config_module.settings.rate_limit_burst = 5

    async def test_expire_keeps_key_ttl_within_bound(self, mock_redis):
        """After one hit, the key's TTL is bounded (not unbounded, not zero)."""
        with patch("app.services.rate_limit.get_redis", return_value=mock_redis):
            await enforce_rate_limit("user-ttl")
            keys = await mock_redis.keys("*:rl:user-ttl")
            assert keys, "rate limit key not created"
            ttl = await mock_redis.ttl(keys[0])
            # Implementation sets EXPIRE 90; the fake's TTL is the remaining seconds.
            assert 0 < ttl <= 90
