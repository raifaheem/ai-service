import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.memory import (
    _key,
    _owner_key,
    _summary_key,
    _meta_key,
    make_turn,
)


# --------------- Key functions ---------------

def test_key_format():
    k = _key("abc-123")
    assert "abc-123" in k
    assert k.endswith(":turns")


def test_owner_key_format():
    k = _owner_key("abc-123")
    assert "abc-123" in k
    assert k.endswith(":owner")


def test_summary_key_format():
    k = _summary_key("abc-123")
    assert "abc-123" in k
    assert k.endswith(":summary")


def test_meta_key_format():
    k = _meta_key("abc-123")
    assert "abc-123" in k
    assert k.endswith(":meta")


# --------------- make_turn ---------------

def test_make_turn():
    t = make_turn("user", "hello")
    assert t.role == "user"
    assert t.content == "hello"
    assert t.ts > 0


# --------------- Summary functions ---------------

@pytest.mark.asyncio
async def test_get_summary():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "Previous context summary"

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import get_summary
        result = await get_summary("conv-123")

    assert result == "Previous context summary"


@pytest.mark.asyncio
async def test_get_summary_none():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import get_summary
        result = await get_summary("conv-123")

    assert result is None


@pytest.mark.asyncio
async def test_set_summary():
    mock_redis = AsyncMock()

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import set_summary
        await set_summary("conv-123", "Test summary")

    mock_redis.set.assert_called_once()
    args = mock_redis.set.call_args
    assert "summary" in args[0][0]
    assert args[0][1] == "Test summary"


# --------------- Metadata functions ---------------

@pytest.mark.asyncio
async def test_get_metadata():
    meta = {"topic": "symptom_check", "turn_count": 4}
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps(meta)

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import get_metadata
        result = await get_metadata("conv-123")

    assert result == meta


@pytest.mark.asyncio
async def test_get_metadata_none():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import get_metadata
        result = await get_metadata("conv-123")

    assert result is None


@pytest.mark.asyncio
async def test_update_metadata():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps({"topic": "general_health", "turn_count": 2})

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import update_metadata
        result = await update_metadata("conv-123", topic="symptom_check", turn_count=4)

    assert result["topic"] == "symptom_check"
    assert result["turn_count"] == 4
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_update_metadata_creates_new():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.services.memory.get_redis", return_value=mock_redis):
        from app.services.memory import update_metadata
        result = await update_metadata("conv-123", topic="fitness")

    assert result == {"topic": "fitness"}
