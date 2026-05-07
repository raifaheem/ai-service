"""Generic async-context-manager wrapping a DistributedCircuitBreaker around any async call.

Centralizes the "check is_available → run → record_success/record_failure" pattern so
each consumer (Qdrant, OpenAI) writes one line instead of three. Reused by
[openai_call_guard.py](openai_call_guard.py) and the Qdrant call sites in
[vector_store.py](vector_store.py).

Semantics:
- If the breaker is open, raise `unavailable_exc()` *before* the wrapped call runs —
  no upstream traffic, fail-fast.
- If the wrapped call raises one of `recorded_excs`, record a failure and re-raise
  (caller decides how to handle).
- If the wrapped call succeeds, record a success.
- Other exceptions pass through *without* affecting the breaker — they aren't
  upstream-health signals.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .circuit_breaker import DistributedCircuitBreaker


@asynccontextmanager
async def breaker_guard(
    breaker: DistributedCircuitBreaker,
    unavailable_exc: type[BaseException],
    recorded_excs: tuple[type[BaseException], ...],
) -> AsyncIterator[None]:
    if not await breaker.is_available:
        raise unavailable_exc(f"Circuit breaker '{breaker.name}' is open")
    try:
        yield
    except recorded_excs:
        await breaker.record_failure()
        raise
    else:
        await breaker.record_success()
