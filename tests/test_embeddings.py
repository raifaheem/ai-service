import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.embeddings import (
    embed_text,
    embed_texts,
    normalize_text_for_embedding,
    _emb_cache_key,
)


# --------------- normalize_text_for_embedding ---------------

def test_normalize_text_for_embedding_collapses_whitespace():
    assert normalize_text_for_embedding("  hello   world  ") == "hello world"


def test_normalize_text_for_embedding_tabs_and_newlines():
    assert normalize_text_for_embedding("hello\t\nworld") == "hello world"


def test_normalize_text_for_embedding_empty():
    assert normalize_text_for_embedding("   ") == ""


# --------------- _emb_cache_key ---------------

def test_emb_cache_key_deterministic():
    k1 = _emb_cache_key("hello world")
    k2 = _emb_cache_key("hello world")
    assert k1 == k2


def test_emb_cache_key_differs():
    k1 = _emb_cache_key("hello")
    k2 = _emb_cache_key("world")
    assert k1 != k2


def test_emb_cache_key_has_prefix():
    key = _emb_cache_key("test")
    assert key.startswith("healthai:emb:")


# --------------- embed_text ---------------

def _mock_embedding_response(vector: list[float]):
    item = MagicMock()
    item.embedding = vector
    resp = MagicMock()
    resp.data = [item]
    return resp


@pytest.mark.asyncio
async def test_embed_text_calls_openai():
    fake_vector = [0.1, 0.2, 0.3]
    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_embedding_response(fake_vector)
        result = await embed_text("hello world")

    assert result == fake_vector
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_embed_text_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        await embed_text("   ")


@pytest.mark.asyncio
async def test_embed_text_no_redis_works():
    fake_vector = [0.1, 0.2, 0.3]
    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_embedding_response(fake_vector)
        result = await embed_text("test", redis_client=None)

    assert result == fake_vector


@pytest.mark.asyncio
async def test_embed_text_cache_miss_stores():
    fake_vector = [0.1, 0.2, 0.3]
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = _mock_embedding_response(fake_vector)
        result = await embed_text("hello", redis_client=mock_redis)

    assert result == fake_vector
    mock_redis.get.assert_called_once()
    mock_redis.set.assert_called_once()
    # Verify the stored value is the JSON-serialized vector
    stored_value = mock_redis.set.call_args[0][1]
    assert json.loads(stored_value) == fake_vector


@pytest.mark.asyncio
async def test_embed_text_cache_hit_skips_openai():
    cached_vector = [0.4, 0.5, 0.6]
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps(cached_vector)

    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        result = await embed_text("hello", redis_client=mock_redis)

    assert result == cached_vector
    mock_create.assert_not_called()


# --------------- embed_texts ---------------

@pytest.mark.asyncio
async def test_embed_texts_calls_openai():
    fake_vectors = [[0.1, 0.2], [0.3, 0.4]]
    items = [MagicMock(embedding=v) for v in fake_vectors]
    resp = MagicMock()
    resp.data = items

    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = resp
        result = await embed_texts(["hello", "world"])

    assert result == fake_vectors


@pytest.mark.asyncio
async def test_embed_texts_empty_returns_empty():
    result = await embed_texts([])
    assert result == []


@pytest.mark.asyncio
async def test_embed_texts_with_cache_partial_hit():
    cached_vector = [0.1, 0.2]
    new_vector = [0.3, 0.4]

    mock_redis = AsyncMock()
    # First text is cached, second is not
    call_count = 0

    async def mock_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return json.dumps(cached_vector)
        return None

    mock_redis.get = mock_get

    items = [MagicMock(embedding=new_vector)]
    resp = MagicMock()
    resp.data = items

    with patch("app.services.embeddings.client.embeddings.create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = resp
        result = await embed_texts(["cached text", "new text"], redis_client=mock_redis)

    assert result[0] == cached_vector
    assert result[1] == new_vector
    # Only one text should be sent to OpenAI
    mock_create.assert_called_once()
    call_input = mock_create.call_args[1]["input"]
    assert len(call_input) == 1
