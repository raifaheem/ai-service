"""Singleton AsyncOpenAI client with lifespan management.

Before A1, `client = create_openai_client()` ran at *import time* — every consumer
that imported `from .openai_client import client` got an eagerly-built AsyncOpenAI,
in violation of the lifespan-managed singleton convention used for Redis and Qdrant
(CLAUDE.md: "never instantiate AsyncOpenAI directly").

This module now exposes `init_openai` / `close_openai` for app/main.py:lifespan, and
`get_openai` for direct access. The legacy `client` symbol is preserved as a proxy
that delegates to the singleton on attribute access — keeps `client.chat.completions.create`
patches in existing tests working without 30+ patch-path rewrites. The proxy itself is
inert at import time; the real AsyncOpenAI is constructed only at first `get_openai()`
call (or `init_openai()` from lifespan, whichever fires first).
"""

from openai import AsyncOpenAI

from ..config import settings

_client: AsyncOpenAI | None = None


def create_openai_client() -> AsyncOpenAI:
    """Construct a new AsyncOpenAI client with retry/timeout settings from config."""
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        max_retries=settings.openai_max_retries,
        timeout=float(settings.openai_timeout),
    )


async def init_openai() -> None:
    """Initialize the singleton from app lifespan startup. Idempotent."""
    global _client
    if _client is None:
        _client = create_openai_client()


async def close_openai() -> None:
    """Close the singleton during lifespan shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.close()
        finally:
            _client = None


def get_openai() -> AsyncOpenAI:
    """Return the singleton; lazy-initialize on first access if lifespan didn't run.

    The lazy fallback exists so test code that patches deep attributes
    (`app.services.intent.client.chat.completions.create`) still resolves
    without each test having to call `init_openai()` first.
    """
    global _client
    if _client is None:
        _client = create_openai_client()
    return _client


class _LazyClient:
    """Module-level shim that delegates attribute access to the singleton.

    Exists purely so `from .openai_client import client` keeps working. Every
    `client.<attr>` lookup goes through `get_openai()`, which triggers lazy init
    if needed. No eager AsyncOpenAI construction at import time.
    """

    def __getattr__(self, name: str):
        return getattr(get_openai(), name)


client = _LazyClient()
