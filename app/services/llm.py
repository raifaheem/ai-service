from typing import Optional, List, AsyncGenerator, Dict, Any

from openai import AsyncOpenAI

from ..config import settings
from .i18n import get_system_prompt, get_rag_instruction, normalize_locale

client = AsyncOpenAI(api_key=settings.openai_api_key)


def _build_system_prompt(locale: str, rag_context: str | None = None) -> str:
    loc = normalize_locale(locale)
    base_prompt = get_system_prompt(loc)

    if not rag_context:
        return base_prompt

    return base_prompt + get_rag_instruction(loc, rag_context)


def _build_user_block(
    user_message: str,
    profile_text: Optional[str] = None,
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
            f"Профиль пользователя (если полезно, без лишних деталей):\n{profile_text}\n\n"
            f"Запрос:\n{user_message}"
        )

    return user_message


async def generate_health_answer(
    user_message: str,
    locale: str = "ru",
    profile_text: Optional[str] = None,
    history: Optional[List[dict]] = None,
    rag_context: Optional[str] = None,
) -> str:
    system_prompt = _build_system_prompt(locale=locale, rag_context=rag_context)
    user_block = _build_user_block(
        user_message=user_message,
        profile_text=profile_text,
        locale=locale,
    )

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.4,
        max_tokens=700,
    )
    return (resp.choices[0].message.content or "").strip()


async def stream_health_answer(
    user_message: str,
    locale: str = "ru",
    profile_text: Optional[str] = None,
    history: Optional[List[dict]] = None,
    rag_context: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    system_prompt = _build_system_prompt(locale=locale, rag_context=rag_context)
    user_block = _build_user_block(
        user_message=user_message,
        profile_text=profile_text,
        locale=locale,
    )

    model_name: Optional[str] = None
    finish_reason: Optional[str] = None
    usage_dict: Optional[dict] = None

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    stream = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.4,
        max_tokens=700,
        stream=True,
        stream_options={"include_usage": True},
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

    if usage_dict:
        yield {
            "type": "usage",
            "usage": usage_dict,
            "model": model_name,
            "finish_reason": finish_reason,
        }