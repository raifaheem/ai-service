import os

os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["SERVICE_TOKEN"] = "test-token"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["QDRANT_URL"] = "http://localhost:6333"

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.schemas import ChatRequest, UserProfile


# --------------- Mock Redis ---------------

@pytest.fixture
def mock_redis():
    """A mock Redis client with sensible defaults."""
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    r.keys = AsyncMock(return_value=[])
    r.lrange = AsyncMock(return_value=[])
    r.rpush = AsyncMock(return_value=1)
    r.ltrim = AsyncMock()
    r.expire = AsyncMock()
    r.ttl = AsyncMock(return_value=3600)
    r.incr = AsyncMock(return_value=1)

    pipe = AsyncMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.rpush = MagicMock()
    pipe.ltrim = MagicMock()
    pipe.set = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, True])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    r.pipeline = MagicMock(return_value=pipe)
    return r


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
    with patch("app.services.redis_client._redis", mock_redis), \
         patch("app.services.redis_client.get_redis", return_value=mock_redis), \
         patch("app.services.vector_client._qdrant", mock_qdrant), \
         patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant), \
         patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock):
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
