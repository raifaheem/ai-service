"""Distributed circuit breaker with Redis-backed shared state.

Each gunicorn worker / horizontal instance used to carry its own in-process
state, so a 4-worker deployment needed three extra failure-storms before the
contour opened uniformly. This module stores state in Redis, with a short
local cache to keep the per-request read cost low.

State transitions (same as a classic CB):
  closed      → open         after `failure_threshold` failures
  open        → half_open    after `recovery_timeout` seconds of quiet
  half_open   → closed       on one successful call
  half_open   → open         on one failure

Redis format (one HASH per breaker name, prefixed by settings.redis_prefix):
  {prefix}:cb:{name}  →  HASH {state, failure_count, last_failure_time}

Design choices:
- **Atomic increments.** `record_failure` uses `HINCRBY` so two workers
  recording failures concurrently can't under-count by racing on a
  read-modify-write. State transitions are idempotent (both workers may
  write the same `state=open` value, which is fine).
- Fail-open on Redis errors. The breaker exists to protect upstream services;
  it must not itself become a SPOF when Redis blips.
- Local-cache TTL of 1s caps Redis round-trips at ~1/s/worker per breaker,
  which is negligible next to the request rate limit.
"""

import asyncio
import logging
import time
from typing import Any, Literal, cast

from .redis_client import get_redis

logger = logging.getLogger(__name__)

State = Literal["closed", "open", "half_open"]

_REDIS_TTL_SECONDS = 300


class DistributedCircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: int = 60,
        local_cache_ttl: float = 1.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._local_cache_ttl = local_cache_ttl

        self._local_state: State = "closed"
        self._local_failure_count = 0
        self._local_last_failure: float = 0.0
        self._last_sync: float = 0.0
        self._lock = asyncio.Lock()

    # --- Redis helpers ---

    def _key(self) -> str:
        from ..config import settings

        return f"{settings.redis_prefix}:cb:{self.name}"

    @staticmethod
    def _decode(value):
        if isinstance(value, bytes):
            return value.decode()
        return value

    async def _sync_from_redis(self) -> None:
        try:
            # redis-py async stubs declare hgetall as `Awaitable[dict] | dict`;
            # cast to satisfy the type checker without runtime cost.
            raw = await cast(Any, get_redis().hgetall(self._key()))
        except Exception:
            # Redis itself is failing — keep whatever local state we have and
            # let the request proceed; the breaker must not escalate outages.
            logger.debug("CB %s: sync-from-redis failed, using local state", self.name)
            self._last_sync = time.time()
            return

        data: dict[str, str] = {self._decode(k): self._decode(v) for k, v in (raw or {}).items()}
        if not data:
            self._local_state = "closed"
            self._local_failure_count = 0
            self._local_last_failure = 0.0
            self._last_sync = time.time()
            return

        try:
            self._local_state = cast(State, data.get("state", "closed"))
            self._local_failure_count = int(data.get("failure_count", "0"))
            self._local_last_failure = float(data.get("last_failure_time", "0.0"))
        except (TypeError, ValueError):
            logger.warning("CB %s: corrupt state in Redis, resetting", self.name)
            self._local_state = "closed"
            self._local_failure_count = 0
            self._local_last_failure = 0.0
            self._last_sync = time.time()
            return

        # Transition open → half_open once the recovery window has passed.
        if self._local_state == "open" and time.time() - self._local_last_failure >= self.recovery_timeout:
            self._local_state = "half_open"

        self._last_sync = time.time()

    async def _hset_state(self) -> None:
        """Persist state + last_failure_time (caller decided the values)."""
        try:
            r = get_redis()
            await cast(
                Any,
                r.hset(
                    self._key(),
                    mapping={
                        "state": self._local_state,
                        "failure_count": str(self._local_failure_count),
                        "last_failure_time": str(self._local_last_failure),
                    },
                ),
            )
            await cast(Any, r.expire(self._key(), _REDIS_TTL_SECONDS))
        except Exception:
            logger.debug("CB %s: write-to-redis failed", self.name)

    async def _maybe_refresh(self) -> None:
        if time.time() - self._last_sync <= self._local_cache_ttl:
            return
        async with self._lock:
            if time.time() - self._last_sync > self._local_cache_ttl:
                await self._sync_from_redis()

    # --- Public API (async) ---

    @property
    async def state(self) -> State:
        await self._maybe_refresh()
        return self._local_state

    @property
    async def is_available(self) -> bool:
        return (await self.state) != "open"

    async def record_success(self) -> None:
        was_half_open = self._local_state == "half_open"
        self._local_state = "closed"
        self._local_failure_count = 0
        self._local_last_failure = 0.0
        self._last_sync = time.time()
        await self._hset_state()
        if was_half_open:
            logger.info("Circuit breaker '%s' recovered (half_open -> closed)", self.name)

    async def record_failure(self) -> None:
        now = time.time()
        self._local_last_failure = now

        # HINCRBY makes the failure count Redis-authoritative — racing workers
        # can't read 2, both write 3 (a 1-count loss). On Redis failure, fall
        # back to a local-only count so the breaker still behaves on a single
        # node.
        try:
            r = get_redis()
            new_count = await cast(Any, r.hincrby(self._key(), "failure_count", 1))
        except Exception:
            new_count = self._local_failure_count + 1

        self._local_failure_count = int(new_count)

        was_half_open = self._local_state == "half_open"
        should_open = was_half_open or self._local_failure_count >= self.failure_threshold
        previously = self._local_state
        if should_open:
            self._local_state = "open"

        self._last_sync = time.time()

        try:
            r = get_redis()
            # `failure_count` is already authoritative (HINCRBY); rewriting it here is
            # redundant but cheap and keeps `_hset_state` semantics simple.
            await cast(
                Any,
                r.hset(
                    self._key(),
                    mapping={
                        "state": self._local_state,
                        "last_failure_time": str(self._local_last_failure),
                    },
                ),
            )
            await cast(Any, r.expire(self._key(), _REDIS_TTL_SECONDS))
        except Exception:
            logger.debug("CB %s: write-to-redis failed", self.name)

        if should_open and previously != "open":
            logger.warning(
                "Circuit breaker '%s' opened after %d failures",
                self.name,
                self._local_failure_count,
            )

    async def reset(self) -> None:
        """Force-close the breaker and clear any persisted state."""
        self._local_state = "closed"
        self._local_failure_count = 0
        self._local_last_failure = 0.0
        self._last_sync = time.time()
        try:
            await get_redis().delete(self._key())
        except Exception:
            logger.debug("CB %s: reset delete failed", self.name)


# Backwards-compatible alias so the external import path stays the same.
CircuitBreaker = DistributedCircuitBreaker


openai_breaker = DistributedCircuitBreaker("openai")
qdrant_breaker = DistributedCircuitBreaker("qdrant")

DEGRADED_MESSAGES = {
    "ru": "Сервис временно перегружен. Пожалуйста, попробуйте позже.",
    "en": "Service is temporarily overloaded. Please try again later.",
    "kk": "Сервис уақытша шамадан тыс жүктелген. Кейінірек қайталап көріңіз.",
}
