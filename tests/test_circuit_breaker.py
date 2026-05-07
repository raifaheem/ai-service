"""Tests for the distributed circuit breaker (B.1, A3).

Exercises the Redis-backed state transitions and cross-instance sharing.
After A3 the breaker uses HINCRBY/HSET so failure increments are atomic
across racing workers; the fake below tracks per-key hash maps.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

from app.services.circuit_breaker import DEGRADED_MESSAGES, DistributedCircuitBreaker


class _AsyncRedisFake:
    """Tiny async-Redis stand-in covering the hash ops the breaker uses.

    Each key maps to a string→str dict (matches Redis semantics). HINCRBY is
    serialized through an asyncio.Lock so the race-test below can exercise
    real async-level interleaving without us pretending to be atomic.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def hset(self, key, mapping=None, **kwargs):
        if mapping is None:
            mapping = kwargs
        self.store.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})
        return len(mapping)

    async def hincrby(self, key, field, amount=1):
        # Atomic increment under our local lock — without this two coroutines
        # both reading `field` before either writes would each return amount,
        # which is exactly the bug A3 fixes in the real code.
        async with self._lock:
            slot = self.store.setdefault(key, {})
            current = int(slot.get(field, "0"))
            current += amount
            slot[field] = str(current)
            return current

    async def expire(self, key, ttl):
        return key in self.store

    async def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


def _patch_redis(fake: _AsyncRedisFake):
    return patch("app.services.circuit_breaker.get_redis", return_value=fake)


class TestCircuitBreakerStates:
    async def test_initial_state_is_closed(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", local_cache_ttl=0.0)
        with _patch_redis(fake):
            assert await cb.state == "closed"
            assert await cb.is_available is True

    async def test_stays_closed_under_threshold(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=3, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            await cb.record_failure()
            assert await cb.state == "closed"
            assert await cb.is_available is True

    async def test_opens_at_threshold(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=3, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            await cb.record_failure()
            await cb.record_failure()
            assert await cb.state == "open"
            assert await cb.is_available is False

    async def test_success_resets_count(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=3, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            await cb.record_failure()
            await cb.record_success()
            assert await cb.state == "closed"
            assert cb._local_failure_count == 0

    async def test_open_transitions_to_half_open_after_timeout(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=1, recovery_timeout=1, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            assert await cb.state == "open"

            # Rewrite persisted state so last_failure_time is 2 s in the past; force a
            # resync on the next `state` read.
            cb._last_sync = 0.0
            fake.store[cb._key()] = {
                "state": "open",
                "failure_count": "1",
                "last_failure_time": str(time.time() - 2),
            }
            assert await cb.state == "half_open"
            assert await cb.is_available is True

    async def test_half_open_success_closes(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=1, recovery_timeout=0, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            # Coerce to half_open via backdated state.
            cb._local_state = "half_open"
            await cb.record_success()
            assert await cb.state == "closed"
            assert cb._local_failure_count == 0

    async def test_half_open_failure_reopens(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=1, recovery_timeout=60, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            cb._local_state = "half_open"
            await cb.record_failure()
            assert await cb.state == "open"

    async def test_reset(self):
        fake = _AsyncRedisFake()
        cb = DistributedCircuitBreaker("test", failure_threshold=1, local_cache_ttl=0.0)
        with _patch_redis(fake):
            await cb.record_failure()
            assert await cb.state == "open"

            await cb.reset()
            assert await cb.state == "closed"
            assert cb._local_failure_count == 0
            assert await cb.is_available is True


class TestDistributedState:
    """Two breaker instances with the same name share Redis-backed state.

    Simulates two gunicorn workers seeing the same OpenAI outage.
    """

    async def test_second_instance_sees_open_state(self):
        fake = _AsyncRedisFake()
        cb_a = DistributedCircuitBreaker("shared", failure_threshold=3, local_cache_ttl=0.0)
        cb_b = DistributedCircuitBreaker("shared", failure_threshold=3, local_cache_ttl=0.0)

        with _patch_redis(fake):
            # Worker A records 3 failures and opens.
            await cb_a.record_failure()
            await cb_a.record_failure()
            await cb_a.record_failure()
            assert await cb_a.state == "open"

            # Worker B, which hasn't seen any failures locally, syncs from Redis on
            # its next state read and observes the open contour.
            assert await cb_b.state == "open"
            assert await cb_b.is_available is False

    async def test_concurrent_failures_increment_atomically(self):
        """Two workers recording simultaneous failures must produce count=2, not 1.

        Pre-A3 each instance did `count = local + 1; SET payload`. Two workers
        racing on the same key both wrote 1, losing one failure. With HINCRBY
        the count is Redis-side authoritative.
        """
        fake = _AsyncRedisFake()
        cb_a = DistributedCircuitBreaker("race", failure_threshold=10, local_cache_ttl=0.0)
        cb_b = DistributedCircuitBreaker("race", failure_threshold=10, local_cache_ttl=0.0)

        with _patch_redis(fake):
            await asyncio.gather(cb_a.record_failure(), cb_b.record_failure())

        # The HASH stores the merged count.
        stored = fake.store[cb_a._key()]
        assert int(stored["failure_count"]) == 2


class TestRedisFailureFallback:
    """If Redis blips, the breaker must not itself become an outage source."""

    async def test_sync_failure_keeps_local_state(self):
        cb = DistributedCircuitBreaker("test", local_cache_ttl=0.0)

        failing_redis = AsyncMock()
        failing_redis.hgetall = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch("app.services.circuit_breaker.get_redis", return_value=failing_redis):
            # Fresh breaker + Redis errors → breaker stays closed, service keeps serving.
            assert await cb.is_available is True
            assert await cb.state == "closed"

    async def test_write_failure_does_not_raise(self):
        cb = DistributedCircuitBreaker("test", local_cache_ttl=0.0)

        failing_redis = AsyncMock()
        failing_redis.hgetall = AsyncMock(return_value={})
        failing_redis.hset = AsyncMock(side_effect=ConnectionError("redis down"))
        failing_redis.hincrby = AsyncMock(side_effect=ConnectionError("redis down"))
        failing_redis.expire = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch("app.services.circuit_breaker.get_redis", return_value=failing_redis):
            # record_* must swallow Redis errors; the caller's request continues.
            await cb.record_failure()
            await cb.record_success()


class TestLocalCacheTtl:
    """Reads within the cache window don't round-trip Redis."""

    async def test_cache_prevents_excessive_reads(self):
        fake = _AsyncRedisFake()
        hgetall_calls = {"n": 0}

        class _CountingRedis:
            async def hgetall(self, key):
                hgetall_calls["n"] += 1
                return await fake.hgetall(key)

            async def hset(self, key, mapping=None, **kwargs):
                return await fake.hset(key, mapping=mapping, **kwargs)

            async def expire(self, key, ttl):
                return await fake.expire(key, ttl)

        counting = _CountingRedis()
        cb = DistributedCircuitBreaker("test", local_cache_ttl=5.0)

        with patch("app.services.circuit_breaker.get_redis", return_value=counting):
            await cb.state
            await cb.state
            await cb.state

        # One sync for the first read; next two are served from local cache.
        assert hgetall_calls["n"] == 1


class TestDegradedMessages:
    def test_all_locales_present(self):
        assert {"ru", "en", "kk"}.issubset(DEGRADED_MESSAGES)

    def test_messages_not_empty(self):
        for locale, msg in DEGRADED_MESSAGES.items():
            assert len(msg) > 10, f"Degraded message for {locale} too short"
