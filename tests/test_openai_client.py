"""Singleton lifecycle for the AsyncOpenAI client (A1)."""

import pytest

from app.config import settings
from app.services import openai_client as oc
from app.services.openai_client import client, create_openai_client


@pytest.fixture
def reset_singleton():
    """Opt-in fixture for tests that probe init/close transitions.

    Plain sync fixture: we never `await close_openai` here because previous
    tests may have closed the event loop; we just clear the singleton ref.
    The leaked AsyncOpenAI instance is GC'd by the runtime.
    """
    oc._client = None
    yield
    oc._client = None


# --------------- Legacy proxy semantics (preserved for existing tests) ---------------


def test_client_has_retry_config():
    assert client.max_retries == settings.openai_max_retries


def test_client_has_timeout_config():
    timeout = client.timeout
    # OpenAI SDK stores timeout as either a float or a Timeout object.
    if isinstance(timeout, int | float):
        assert timeout == float(settings.openai_timeout)
    else:
        assert timeout.connect == float(settings.openai_timeout)


def test_create_openai_client_returns_instance():
    c = create_openai_client()
    assert c is not None
    assert c.max_retries == settings.openai_max_retries


# --------------- Lifespan-managed singleton (A1) ---------------


async def test_init_creates_singleton(reset_singleton):
    assert oc._client is None
    await oc.init_openai()
    assert oc._client is not None


async def test_init_is_idempotent(reset_singleton):
    await oc.init_openai()
    first = oc._client
    await oc.init_openai()
    assert oc._client is first  # second call did not replace the instance


async def test_close_clears_singleton(reset_singleton):
    await oc.init_openai()
    assert oc._client is not None
    await oc.close_openai()
    assert oc._client is None


async def test_close_is_safe_when_uninitialized(reset_singleton):
    assert oc._client is None
    # Must not raise even though there is no client to close.
    await oc.close_openai()
    assert oc._client is None


async def test_get_openai_lazy_initializes(reset_singleton):
    assert oc._client is None
    instance = oc.get_openai()
    assert instance is not None
    assert oc._client is instance  # populated as a side effect


async def test_get_openai_returns_singleton(reset_singleton):
    a = oc.get_openai()
    b = oc.get_openai()
    assert a is b


async def test_legacy_client_proxy_delegates(reset_singleton):
    """The `client` proxy must forward attribute access to the singleton."""
    await oc.init_openai()
    # `chat` on the proxy resolves to `chat` on the underlying AsyncOpenAI.
    assert oc.client.chat is oc._client.chat


async def test_no_eager_construction_on_module_import(reset_singleton):
    """Re-importing the module must NOT auto-create a real AsyncOpenAI."""
    import importlib

    importlib.reload(oc)
    assert oc._client is None
