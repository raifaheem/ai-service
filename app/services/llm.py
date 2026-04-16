import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, cast

from ..config import settings
from ..metrics import metrics
from .i18n import get_rag_instruction, get_system_prompt, normalize_locale
from .openai_client import client

logger = logging.getLogger(__name__)


def _build_system_prompt(
    locale: str,
    rag_context: str | None = None,
    addon_prompt: str | None = None,
) -> str:
    loc = normalize_locale(locale)
    base_prompt = get_system_prompt(loc)

    if addon_prompt:
        base_prompt = base_prompt + "\n\n" + addon_prompt

    if not rag_context:
        return base_prompt

    return base_prompt + get_rag_instruction(loc, rag_context)


def _build_user_block(
    user_message: str,
    profile_text: str | None = None,
    locale: str = "ru",
) -> str:
    loc = normalize_locale(locale)

    if profile_text:
        if loc == "en":
            return (
                f"User profile (use only if helpful, avoid unnecessary details):\n{profile_text}\n\n"
                f"User request:\n{user_message}"
            )
        if loc == "kk":
            return (
                f"Пайдаланушы профилі (тек қажет болса қолдан, артық деталь қоспа):\n{profile_text}\n\n"
                f"Сұрағы:\n{user_message}"
            )
        return (
            f"Профиль пользователя (если полезно, без лишних деталей):\n{profile_text}\n\n" f"Запрос:\n{user_message}"
        )

    return user_message


async def generate_health_answer(
    user_message: str,
    locale: str = "ru",
    profile_text: str | None = None,
    history: list[dict] | None = None,
    rag_context: str | None = None,
    addon_prompt: str | None = None,
    temperature: float = 0.4,
    summary: str | None = None,
) -> str:
    system_prompt = _build_system_prompt(locale=locale, rag_context=rag_context, addon_prompt=addon_prompt)
    user_block = _build_user_block(
        user_message=user_message,
        profile_text=profile_text,
        locale=locale,
    )

    messages: list[Any] = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": f"Previous conversation summary:\n{summary}"})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    start = time.perf_counter()
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=temperature,
        max_tokens=settings.max_response_tokens,
    )
    duration_ms = round((time.perf_counter() - start) * 1000, 1)

    usage = resp.usage
    if usage:
        logger.info(
            "OpenAI usage",
            extra={
                "openai_model": resp.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "duration_ms": duration_ms,
                "call_type": "generate",
            },
        )
        metrics.record_openai_usage(usage.prompt_tokens, usage.completion_tokens)

    return (resp.choices[0].message.content or "").strip()


async def stream_health_answer(
    user_message: str,
    locale: str = "ru",
    profile_text: str | None = None,
    history: list[dict] | None = None,
    rag_context: str | None = None,
    addon_prompt: str | None = None,
    temperature: float = 0.4,
    summary: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    system_prompt = _build_system_prompt(locale=locale, rag_context=rag_context, addon_prompt=addon_prompt)
    user_block = _build_user_block(
        user_message=user_message,
        profile_text=profile_text,
        locale=locale,
    )

    model_name: str | None = None
    finish_reason: str | None = None
    usage_dict: dict | None = None

    messages: list[Any] = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": f"Previous conversation summary:\n{summary}"})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    start = time.perf_counter()
    stream = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=temperature,
        max_tokens=settings.max_response_tokens,
        stream=True,
        stream_options=cast(Any, {"include_usage": True}),
    )

    async for chunk in stream:
        if model_name is None and hasattr(chunk, "model"):
            model_name = getattr(chunk, "model", None)

        if getattr(chunk, "choices", None):
            choice0 = chunk.choices[0]
            if getattr(choice0, "finish_reason", None):
                finish_reason = choice0.finish_reason
            delta = getattr(getattr(choice0, "delta", None), "content", None)
            if delta:
                yield {"type": "delta", "text": delta}

        if getattr(chunk, "usage", None):
            u = chunk.usage
            usage_dict = {
                "prompt_tokens": getattr(u, "prompt_tokens", None),
                "completion_tokens": getattr(u, "completion_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
            }

    duration_ms = round((time.perf_counter() - start) * 1000, 1)

    if usage_dict:
        logger.info(
            "OpenAI usage (stream)",
            extra={
                "openai_model": model_name,
                "prompt_tokens": usage_dict.get("prompt_tokens"),
                "completion_tokens": usage_dict.get("completion_tokens"),
                "total_tokens": usage_dict.get("total_tokens"),
                "duration_ms": duration_ms,
                "call_type": "stream",
            },
        )
        metrics.record_openai_usage(
            usage_dict.get("prompt_tokens", 0) or 0,
            usage_dict.get("completion_tokens", 0) or 0,
        )
        yield {
            "type": "usage",
            "usage": usage_dict,
            "model": model_name,
            "finish_reason": finish_reason,
        }
