import os

os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["SERVICE_TOKEN"] = "test-token"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["QDRANT_URL"] = "http://localhost:6333"

import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.schemas import UserProfile

# --------------- Stateful mock Redis ---------------


class _FakeRedis:
    """Stateful in-memory stand-in for async Redis used in tests.

    Models the handful of behaviours tests actually depend on:
    - strings (GET/SET/DELETE) with TTL,
    - lists (RPUSH/LTRIM/LRANGE),
    - INCR/EXPIRE,
    - KEYS glob (trivial prefix+suffix),
    - TTL semantics: -2 for missing keys, -1 for no-expiry, >=0 otherwise,
    - pipelines that accumulate and replay commands atomically.
    """

    def __init__(self) -> None:
        self._store: dict[str, object] = {}
        self._expiries: dict[str, float] = {}

    def _evict_if_expired(self, key: str) -> None:
        deadline = self._expiries.get(key)
        if deadline is not None and deadline <= time.time():
            self._store.pop(key, None)
            self._expiries.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        self._evict_if_expired(key)
        val = self._store.get(key)
        if isinstance(val, list):
            return None
        return val

    async def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        self._evict_if_expired(key)
        if nx and key in self._store:
            return None
        self._store[key] = value
        if ex is not None:
            self._expiries[key] = time.time() + ex
        else:
            self._expiries.pop(key, None)
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._store:
                removed += 1
                self._store.pop(key, None)
                self._expiries.pop(key, None)
        return removed

    async def keys(self, pattern: str) -> list[str]:
        import fnmatch

        return [k for k in self._store if fnmatch.fnmatchcase(k, pattern)]

    async def lrange(self, key: str, start: int, end: int) -> list:
        self._evict_if_expired(key)
        items = self._store.get(key)
        if not isinstance(items, list):
            return []
        if end == -1:
            return list(items[start:])
        return list(items[start : end + 1])

    async def rpush(self, key: str, *values) -> int:
        self._evict_if_expired(key)
        lst = self._store.get(key)
        if not isinstance(lst, list):
            lst = []
            self._store[key] = lst
        lst.extend(values)
        return len(lst)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self._evict_if_expired(key)
        lst = self._store.get(key)
        if not isinstance(lst, list):
            return True
        if end == -1:
            self._store[key] = lst[start:]
        else:
            self._store[key] = lst[start : end + 1]
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        if key not in self._store:
            return False
        self._expiries[key] = time.time() + ttl
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        self._evict_if_expired(key)
        current = int(self._store.get(key) or 0)
        current += amount
        self._store[key] = current
        return current

    async def ttl(self, key: str) -> int:
        self._evict_if_expired(key)
        if key not in self._store:
            return -2
        deadline = self._expiries.get(key)
        if deadline is None:
            return -1
        return max(0, int(deadline - time.time()))

    # --- sorted sets (ZSET) — used by sliding-window rate limiting ---

    def _zset(self, key: str) -> dict[str, float]:
        self._evict_if_expired(key)
        store = self._store.get(key)
        if not isinstance(store, dict):
            store = {}
            self._store[key] = store
        return store

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zset = self._zset(key)
        added = 0
        for member, score in mapping.items():
            if member not in zset:
                added += 1
            zset[member] = float(score)
        return added

    async def zcard(self, key: str) -> int:
        self._evict_if_expired(key)
        zset = self._store.get(key)
        if not isinstance(zset, dict):
            return 0
        return len(zset)

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        self._evict_if_expired(key)
        zset = self._store.get(key)
        if not isinstance(zset, dict):
            return 0
        to_drop = [m for m, s in zset.items() if min_score <= s <= max_score]
        for m in to_drop:
            zset.pop(m, None)
        return len(to_drop)

    def pipeline(self, transaction: bool = True) -> "_FakePipeline":
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *_) -> bool:
        return False

    def _queue(self, name: str):
        def _op(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return _op

    def __getattr__(self, name: str):
        return self._queue(name)

    async def execute(self) -> list:
        results: list = []
        for name, args, kwargs in self._ops:
            fn = getattr(self._redis, name)
            results.append(await fn(*args, **kwargs))
        self._ops.clear()
        return results


@pytest.fixture
def mock_redis():
    """Stateful async Redis stand-in. Each test gets a fresh instance."""
    return _FakeRedis()


# --------------- Mock Qdrant ---------------


@pytest.fixture
def mock_qdrant():
    """A mock Qdrant client."""
    q = AsyncMock()
    collection_info = MagicMock()
    collection_info.points_count = 42
    q.get_collections = AsyncMock()
    q.get_collection = AsyncMock(return_value=collection_info)
    q.search = AsyncMock(return_value=[])
    q.upsert = AsyncMock()
    return q


# --------------- Mock OpenAI ---------------


@dataclass
class _MockUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20
    total_tokens: int = 30


@dataclass
class _MockMessage:
    content: str = "Test LLM response"


@dataclass
class _MockChoice:
    message: _MockMessage = None
    finish_reason: str = "stop"

    def __post_init__(self):
        if self.message is None:
            self.message = _MockMessage()


@dataclass
class _MockCompletion:
    choices: list = None
    usage: _MockUsage = None
    model: str = "gpt-4o-mini"

    def __post_init__(self):
        if self.choices is None:
            self.choices = [_MockChoice()]
        if self.usage is None:
            self.usage = _MockUsage()


@pytest.fixture
def mock_openai_client():
    """A mock OpenAI client that returns a canned completion."""
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_MockCompletion())
    return client


# --------------- Auth helpers ---------------


@pytest.fixture
def service_auth_headers():
    """Headers for service token auth."""
    return {"X-Service-Token": "test-token", "X-User-Id": "test-user-123"}


@pytest.fixture
def sample_chat_request():
    """A minimal valid ChatRequest dict."""
    return {"message": "What helps with headaches?", "locale": "en"}


@pytest.fixture
def sample_profile():
    """A sample UserProfile."""
    return UserProfile(age=30, sex="male", conditions=["diabetes"], goals=["lose weight"])


# --------------- Patched app client ---------------


@pytest.fixture
def patched_app(mock_redis, mock_qdrant):
    """Patch Redis and Qdrant before importing the app, return the FastAPI instance."""
    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
    ):
        from app.main import app

        yield app


@pytest.fixture
async def auth_client(patched_app, service_auth_headers):
    """An AsyncClient with service-token auth headers pre-configured."""
    transport = ASGITransport(app=patched_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=service_auth_headers,
    ) as client:
        yield client
