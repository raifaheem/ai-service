"""Extended memory service tests — covers async Redis operations."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.memory import (
    get_history,
    append_turns,
    get_owner,
    get_ttl,
    get_metadata,
    set_metadata,
    delete_conversation,
    make_turn,
)


class TestGetHistory:
    async def test_returns_turns(self):
        mock_redis = AsyncMock()
        turns = [
            json.dumps({"role": "user", "content": "Hi", "ts": 1000.0}),
            json.dumps({"role": "assistant", "content": "Hello!", "ts": 1001.0}),
        ]
        mock_redis.lrange = AsyncMock(return_value=turns)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_history("conv-1")

        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content == "Hi"
        assert result[1].role == "assistant"

    async def test_empty_history(self):
        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_history("conv-1")

        assert result == []

    async def test_skips_corrupt_turns(self):
        mock_redis = AsyncMock()
        turns = [
            json.dumps({"role": "user", "content": "Valid", "ts": 1000.0}),
            "not-valid-json",
            json.dumps({"role": "assistant", "content": "Also valid", "ts": 1002.0}),
        ]
        mock_redis.lrange = AsyncMock(return_value=turns)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_history("conv-1")

        assert len(result) == 2


class TestAppendTurns:
    async def test_appends_turns(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.rpush = MagicMock()
        pipe.ltrim = MagicMock()
        pipe.expire = MagicMock()
        pipe.set = MagicMock()
        pipe.execute = AsyncMock(return_value=[2, True, True, True, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        turns = [make_turn("user", "Hello"), make_turn("assistant", "Hi")]

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            await append_turns("conv-1", turns, user_id="user-1")

        pipe.rpush.assert_called_once()
        pipe.ltrim.assert_called_once()

    async def test_empty_turns_noop(self):
        mock_redis = AsyncMock()

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            await append_turns("conv-1", [])

        mock_redis.pipeline.assert_not_called()

    async def test_without_user_id(self):
        mock_redis = AsyncMock()
        pipe = AsyncMock()
        pipe.rpush = MagicMock()
        pipe.ltrim = MagicMock()
        pipe.expire = MagicMock()
        pipe.set = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, True, True, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=pipe)

        turns = [make_turn("user", "Test")]

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            await append_turns("conv-1", turns, user_id=None)

        # set should not be called for owner when user_id is None
        pipe.set.assert_not_called()


class TestGetOwner:
    async def test_returns_owner(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"user-123")

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_owner("conv-1")

        assert result == "user-123"

    async def test_returns_none(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_owner("conv-1")

        assert result is None

    async def test_returns_string_value(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="user-str")

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_owner("conv-1")

        assert result == "user-str"


class TestGetTtl:
    async def test_returns_ttl(self):
        mock_redis = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=3600)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_ttl("conv-1")

        assert result == 3600

    async def test_returns_minus_two_when_key_missing(self):
        mock_redis = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=-2)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_ttl("conv-1")

        assert result == -2


class TestSetMetadata:
    async def test_sets_metadata(self):
        mock_redis = AsyncMock()

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            await set_metadata("conv-1", {"topic": "fitness"})

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert "meta" in call_args[0][0]
        parsed = json.loads(call_args[0][1])
        assert parsed["topic"] == "fitness"


class TestGetMetadataCorrupt:
    async def test_returns_none_on_corrupt_json(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="not-valid-json{{{")

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await get_metadata("conv-1")

        assert result is None


class TestDeleteConversation:
    async def test_deletes_all_keys(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=4)

        with patch("app.services.memory.get_redis", return_value=mock_redis):
            result = await delete_conversation("conv-1")

        assert result == 4
        mock_redis.delete.assert_called_once()
        # Should delete 4 keys: turns, owner, summary, meta
        args = mock_redis.delete.call_args[0]
        assert len(args) == 4
