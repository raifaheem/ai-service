"""SSE event-generator for `/v1/chat/stream` (L4).

Pre-L4 the entire SSE pipeline lived inside [app/routers/chat.py](app/routers/chat.py)
as a closure inside the route handler — 130 lines deeply nested in `chat_stream`,
which made the router 800+ lines and the streaming logic hard to test in
isolation. This module owns:

- `StreamContext` — frozen dataclass collecting every per-request value the
  generator needs (conversation_id, intent, RAG output, prompt addons, …).
  Lets the router build context once and hand it off.
- `sse(event, data)` — the on-the-wire SSE format helper. Injects the
  contextvars-scoped `request_id` so every event carries it (M3 doc'd this).
- `chat_event_generator(ctx, request)` — the async generator. Yields `meta`,
  `delta`, `usage`, `final` / `error` events; honors graceful shutdown and
  client-disconnect mid-stream (B.6 stream-cancellation).

The router is now a thin orchestration layer that builds the context, decides
which short-circuit generator to use (injection refusal, off-topic, degraded),
or hands off to `chat_event_generator` for the real LLM stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Request
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)

from ..context import get_request_id
from ..lifecycle import is_shutting_down, register_stream
from ..services import memory
from ..services.circuit_breaker import DEGRADED_MESSAGES
from ..services.content_filter import check_response_safety
from ..services.intent import IntentResult
from ..services.llm import stream_health_answer
from ..services.openai_call_guard import OpenAIUnavailable

logger = logging.getLogger(__name__)


def sse(event: str, data: dict) -> str:
    """Format an SSE frame with the contextvars-scoped `request_id` injected.

    Every SSE event in this codebase carries `request_id` so support tickets
    can cite a single id that matches the response header and the application
    log entries (M3 contract). An explicit `request_id` already in `data` wins
    — that's a safety hatch for future overrides.
    """
    payload = {"request_id": get_request_id(), **data}
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass(frozen=True)
class StreamContext:
    """Per-request bundle handed to `chat_event_generator`.

    Frozen so the generator can safely close over it without worrying about
    mid-stream mutation. The fields mirror what the chat router computes
    before it hands off control.
    """

    conversation_id: str
    user_id: str
    locale: str
    user_message: str
    profile_text: str | None
    history_count: int  # `len(history)` at handoff — used for the turn_count audit field
    trimmed_history: list[dict]
    summary: str | None
    rag_context: str
    rag_chunks: list[dict]
    rag_score: float | None
    sources: list[dict]
    addon_prompt: str | None
    intent: IntentResult
    disclaimer: str


def _map_openai_error(e: Exception) -> tuple[str, str]:
    """Return `(code, message)` for an SSE error event.

    Mirrors `map_openai_error` in chat.py but without the HTTP status code
    (SSE doesn't surface it) and with a generic user-facing message — the
    upstream provider/class is information disclosure when echoed verbatim
    (S2).
    """
    generic = "Upstream service unavailable."
    if isinstance(e, RateLimitError):
        return "openai_rate_limit", generic
    if isinstance(e, AuthenticationError):
        return "openai_auth", generic
    if isinstance(e, APIConnectionError):
        return "openai_connection", generic
    if isinstance(e, APIStatusError):
        return "openai_api_status", generic
    return "internal_error", "Internal server error."


async def _persist_stream_turns(ctx: StreamContext, raw_answer: str) -> None:
    """Persist user + assistant turns. Failures swallowed (Redis is ephemeral)."""
    try:
        await memory.append_turns(
            ctx.conversation_id,
            [
                memory.make_turn("user", ctx.user_message),
                memory.make_turn("assistant", raw_answer),
            ],
            user_id=ctx.user_id,
        )
        await memory.update_metadata(
            ctx.conversation_id,
            topic=ctx.intent.category,
            turn_count=ctx.history_count + 2,
        )
    except Exception:
        logger.exception("Failed to persist conversation %s to Redis", ctx.conversation_id)


async def chat_event_generator(ctx: StreamContext, request: Request) -> AsyncGenerator[str, None]:
    """Stream SSE frames for `/v1/chat/stream`.

    The generator's responsibilities:
    1. Register itself for graceful-shutdown wait so `lifespan` can drain it.
    2. Emit `meta` first (always).
    3. Bail with `service_degraded` if the lifespan signaled shutdown between
       handoff and the generator running.
    4. Walk `stream_health_answer`, forwarding deltas as SSE; check
       `request.is_disconnected()` per chunk so the client hanging up stops
       us from burning OpenAI tokens (B.6).
    5. On clean exit: run content safety, persist turns, emit `final`.
    6. On `OpenAIUnavailable` (breaker open): emit `service_degraded` error.
    7. On recorded OpenAI exceptions: map to a code, emit `error` (breaker
       already recorded the failure inside the guard).
    8. On any other exception: log + emit `internal_error`.
    """
    task = asyncio.current_task()
    if task is not None:
        register_stream(task)

    yield sse("meta", {"conversation_id": ctx.conversation_id})

    if is_shutting_down():
        yield sse(
            "error",
            {
                "conversation_id": ctx.conversation_id,
                "code": "service_degraded",
                "message": DEGRADED_MESSAGES.get(ctx.locale, DEGRADED_MESSAGES["ru"]),
            },
        )
        return

    parts: list[str] = []
    usage_payload: dict | None = None
    model_name: str | None = None
    finish_reason: str | None = None

    try:
        client_gone = False
        async for ev in stream_health_answer(
            ctx.user_message,
            locale=ctx.locale,
            profile_text=ctx.profile_text,
            history=ctx.trimmed_history,
            rag_context=ctx.rag_context,
            addon_prompt=ctx.addon_prompt,
            temperature=ctx.intent.temperature,
            summary=ctx.summary,
        ):
            # Cheap ASGI-buffered check — if the client hung up, stop pulling
            # more tokens from OpenAI (saves $$$) and don't persist a half-
            # finished answer.
            if await request.is_disconnected():
                logger.info(
                    "Client disconnected mid-stream, aborting %s",
                    ctx.conversation_id,
                )
                client_gone = True
                break

            ev_type = ev.get("type")
            if ev_type == "delta":
                text = ev.get("text", "")
                if text:
                    parts.append(text)
                    yield sse("delta", {"text": text})
            elif ev_type == "usage":
                usage_payload = ev.get("usage")
                model_name = ev.get("model")
                finish_reason = ev.get("finish_reason")

        if client_gone:
            # Exit silently — the client is no longer reading; no point
            # writing a `final` event or persisting the conversation turn.
            return

        # Breaker recorded success inside `stream_health_answer`'s guard.
        raw_answer = "".join(parts).strip()
        raw_answer, _filters = check_response_safety(raw_answer, locale=ctx.locale)
        answer_to_user = raw_answer
        if ctx.disclaimer.lower() not in answer_to_user.lower():
            answer_to_user = f"{answer_to_user}\n\n{ctx.disclaimer}"

        await _persist_stream_turns(ctx, raw_answer)

        yield sse(
            "final",
            {
                "conversation_id": ctx.conversation_id,
                "answer": answer_to_user,
                "disclaimer": ctx.disclaimer,
                "model": model_name,
                "finish_reason": finish_reason,
                "usage": usage_payload,
                "rag_used": bool(ctx.rag_chunks),
                "rag_score": ctx.rag_score,
                "sources": ctx.sources,
                "intent": {
                    "category": ctx.intent.category,
                    "risk_level": ctx.intent.risk_level,
                    "confidence": ctx.intent.confidence,
                },
            },
        )

    except OpenAIUnavailable:
        yield sse(
            "error",
            {
                "conversation_id": ctx.conversation_id,
                "code": "service_degraded",
                "message": DEGRADED_MESSAGES.get(ctx.locale, DEGRADED_MESSAGES["ru"]),
            },
        )
    except (RateLimitError, APIConnectionError, AuthenticationError, APIStatusError) as e:
        # Breaker already recorded the failure inside the guard.
        code, message = _map_openai_error(e)
        yield sse(
            "error",
            {
                "conversation_id": ctx.conversation_id,
                "code": code,
                "message": message,
            },
        )
    except Exception:
        logger.exception("Unexpected error in chat stream for %s", ctx.conversation_id)
        yield sse(
            "error",
            {
                "conversation_id": ctx.conversation_id,
                "code": "internal_error",
                "message": "Internal server error.",
            },
        )
