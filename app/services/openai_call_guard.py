"""Wrap OpenAI SDK calls in the openai_breaker (A4).

Pre-A4 only `chat.py::generate_health_answer` recorded breaker outcomes; intent,
summarizer, embeddings, triage, and article_analyzer did not. The result was
that during a flapping OpenAI outage the service burned 3+ round-trips per
request (intent + RAG-embed + summarize + generate) before the breaker
threshold was reached.

Use:

    async with openai_call_guard():
        resp = await client.chat.completions.create(...)

The guard:
- raises `OpenAIUnavailable` *before* the wrapped call runs when the breaker is open;
- records a failure on (RateLimitError, APIConnectionError, AuthenticationError, APIStatusError);
- records a success on clean exit;
- lets non-OpenAI exceptions pass through without affecting the breaker.
"""

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)

from .breaker_guard import breaker_guard
from .circuit_breaker import openai_breaker


class OpenAIUnavailable(Exception):
    """Raised when the OpenAI breaker is open."""


OPENAI_RECORDED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
    APIStatusError,
)


def openai_call_guard():
    return breaker_guard(openai_breaker, OpenAIUnavailable, OPENAI_RECORDED_EXCEPTIONS)
