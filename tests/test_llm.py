from unittest.mock import AsyncMock, patch, MagicMock
from dataclasses import dataclass

import pytest

from app.services.llm import _build_system_prompt, _build_user_block


# --------------- _build_system_prompt ---------------

class TestBuildSystemPrompt:
    def test_basic_ru(self):
        prompt = _build_system_prompt(locale="ru")
        assert len(prompt) > 100
        assert isinstance(prompt, str)

    def test_basic_en(self):
        prompt = _build_system_prompt(locale="en")
        assert len(prompt) > 100

    def test_with_rag_context(self):
        prompt = _build_system_prompt(locale="en", rag_context="Some medical info here")
        assert "Some medical info here" in prompt

    def test_without_rag_context_shorter(self):
        prompt_without = _build_system_prompt(locale="en", rag_context=None)
        prompt_with = _build_system_prompt(locale="en", rag_context="Injected RAG data")
        # With RAG context, the prompt should be longer (includes the injected data)
        assert len(prompt_with) > len(prompt_without)
        assert "Injected RAG data" in prompt_with
        assert "Injected RAG data" not in prompt_without

    def test_with_addon_prompt(self):
        prompt = _build_system_prompt(locale="en", addon_prompt="Extra instructions")
        assert "Extra instructions" in prompt

    def test_with_addon_and_rag(self):
        prompt = _build_system_prompt(
            locale="en",
            rag_context="RAG data",
            addon_prompt="Addon text",
        )
        assert "RAG data" in prompt
        assert "Addon text" in prompt

    def test_kk_locale(self):
        prompt = _build_system_prompt(locale="kk")
        assert len(prompt) > 100


# --------------- _build_user_block ---------------

class TestBuildUserBlock:
    def test_simple_message(self):
        block = _build_user_block("Hello doctor")
        assert block == "Hello doctor"

    def test_with_profile_en(self):
        block = _build_user_block("Headache", profile_text="Age: 30", locale="en")
        assert "Age: 30" in block
        assert "Headache" in block
        assert "User profile" in block

    def test_with_profile_ru(self):
        block = _build_user_block("Болит голова", profile_text="Возраст: 30", locale="ru")
        assert "Профиль пользователя" in block
        assert "Возраст: 30" in block

    def test_with_profile_kk(self):
        block = _build_user_block("test", profile_text="Жасы: 30", locale="kk")
        assert "Пайдаланушы профилі" in block
        assert "Жасы: 30" in block

    def test_no_profile(self):
        block = _build_user_block("Just a question", locale="en")
        assert block == "Just a question"


# --------------- generate_health_answer ---------------

@dataclass
class MockUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20
    total_tokens: int = 30


@dataclass
class MockMessage:
    content: str = "Take rest and drink water."


@dataclass
class MockChoice:
    message: MockMessage = None

    def __post_init__(self):
        if self.message is None:
            self.message = MockMessage()


@dataclass
class MockCompletion:
    choices: list = None
    usage: MockUsage = None
    model: str = "gpt-4o-mini"

    def __post_init__(self):
        if self.choices is None:
            self.choices = [MockChoice()]
        if self.usage is None:
            self.usage = MockUsage()


class TestGenerateHealthAnswer:
    async def test_returns_answer(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import generate_health_answer
            result = await generate_health_answer("What helps with headaches?", locale="en")

        assert result == "Take rest and drink water."

    async def test_passes_history(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import generate_health_answer
            await generate_health_answer("Follow up", locale="en", history=history)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    async def test_passes_summary(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import generate_health_answer
            await generate_health_answer("Test", locale="en", summary="Previous summary")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        contents = [m["content"] for m in messages]
        assert any("Previous summary" in c for c in contents)

    async def test_records_metrics(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())
        mock_metrics = MagicMock()

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics", mock_metrics):
            from app.services.llm import generate_health_answer
            await generate_health_answer("Test", locale="en")

        mock_metrics.record_openai_usage.assert_called_once_with(10, 20, call_type="generate")

    async def test_no_usage(self):
        completion = MockCompletion()
        completion.usage = None
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=completion)
        mock_metrics = MagicMock()

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics", mock_metrics):
            from app.services.llm import generate_health_answer
            result = await generate_health_answer("Test", locale="en")

        assert result == "Take rest and drink water."
        mock_metrics.record_openai_usage.assert_not_called()

    async def test_empty_response(self):
        completion = MockCompletion()
        completion.choices[0].message.content = None
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=completion)

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import generate_health_answer
            result = await generate_health_answer("Test", locale="en")

        assert result == ""

    async def test_uses_temperature(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import generate_health_answer
            await generate_health_answer("Test", locale="en", temperature=0.8)

        call_args = mock_client.chat.completions.create.call_args
        assert call_args.kwargs["temperature"] == 0.8


# --------------- stream_health_answer ---------------

class TestStreamHealthAnswer:
    async def test_yields_deltas(self):
        @dataclass
        class MockDelta:
            content: str = "Hello "

        @dataclass
        class MockStreamChoice:
            delta: MockDelta = None
            finish_reason: str = None

            def __post_init__(self):
                if self.delta is None:
                    self.delta = MockDelta()

        @dataclass
        class MockStreamChunk:
            choices: list = None
            model: str = "gpt-4o-mini"
            usage: MockUsage = None

            def __post_init__(self):
                if self.choices is None:
                    self.choices = [MockStreamChoice()]

        chunks = [
            MockStreamChunk(choices=[MockStreamChoice(delta=MockDelta("Hello "))]),
            MockStreamChunk(choices=[MockStreamChoice(delta=MockDelta("world"))]),
            MockStreamChunk(choices=[MockStreamChoice(finish_reason="stop", delta=MockDelta(content=None))]),
            MockStreamChunk(choices=[], usage=MockUsage()),
        ]

        async def mock_stream():
            for c in chunks:
                yield c

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import stream_health_answer

            events = []
            async for ev in stream_health_answer("Test", locale="en"):
                events.append(ev)

        deltas = [e for e in events if e.get("type") == "delta"]
        assert len(deltas) == 2
        assert deltas[0]["text"] == "Hello "
        assert deltas[1]["text"] == "world"

    async def test_yields_usage_event(self):
        @dataclass
        class MockDelta:
            content: str = None

        @dataclass
        class MockStreamChoice:
            delta: MockDelta = None
            finish_reason: str = "stop"

            def __post_init__(self):
                if self.delta is None:
                    self.delta = MockDelta()

        @dataclass
        class MockStreamChunk:
            choices: list = None
            model: str = "gpt-4o-mini"
            usage: MockUsage = None

        chunks = [
            MockStreamChunk(choices=[MockStreamChoice()], usage=None),
            MockStreamChunk(choices=[], usage=MockUsage(), model="gpt-4o-mini"),
        ]

        async def mock_stream():
            for c in chunks:
                yield c

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())

        with patch("app.services.llm.client", mock_client), \
             patch("app.services.llm.metrics"):
            from app.services.llm import stream_health_answer

            events = []
            async for ev in stream_health_answer("Test", locale="en"):
                events.append(ev)

        usage_events = [e for e in events if e.get("type") == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["usage"]["prompt_tokens"] == 10
