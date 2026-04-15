import logging
from typing import Optional

from ..config import settings
from .openai_client import client

logger = logging.getLogger(__name__)

SUMMARY_PROMPTS = {
    "ru": (
        "Кратко резюмируй медицинский контекст из этого разговора (до 150 слов). "
        "Укажи: упомянутые симптомы, обсуждённые состояния, данные рекомендации, "
        "факторы риска, и нужно ли дальнейшее наблюдение. "
        "Пиши от третьего лица ('пользователь упомянул...'). Только факты, без вступлений."
    ),
    "en": (
        "Briefly summarize the medical context from this conversation (up to 150 words). "
        "Include: symptoms mentioned, conditions discussed, recommendations given, "
        "risk factors, and whether follow-up is needed. "
        "Write in third person ('the user mentioned...'). Facts only, no preamble."
    ),
    "kk": (
        "Бұл әңгімеден медициналық контексті қысқаша жинақтаңыз (150 сөзге дейін). "
        "Атап өтіңіз: аталған симптомдар, талқыланған жағдайлар, берілген ұсыныстар, "
        "қауіп факторлары және бақылау қажет пе. "
        "Үшінші жақтан жазыңыз ('пайдаланушы атап өтті...'). Тек фактілер."
    ),
}

# Threshold: when total turns exceed this, trigger summarization of older turns
SUMMARIZE_THRESHOLD = 8
# Keep this many recent turns unsummarized
KEEP_RECENT_TURNS = 6


async def summarize_conversation(
    turns: list[dict],
    locale: str = "ru",
) -> Optional[str]:
    """Summarize a list of conversation turns into a brief medical context summary."""
    if not turns:
        return None

    prompt = SUMMARY_PROMPTS.get(locale, SUMMARY_PROMPTS["ru"])

    conversation_text = ""
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        label = "User" if role == "user" else "Assistant"
        conversation_text += f"{label}: {content}\n"

    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": conversation_text.strip()},
            ],
            temperature=0.2,
            max_tokens=250,
        )
        summary = (resp.choices[0].message.content or "").strip()
        return summary if summary else None
    except Exception:
        logger.exception("Failed to summarize conversation")
        return None


def should_summarize(total_turns: int) -> bool:
    """Check if conversation is long enough to benefit from summarization."""
    return total_turns > SUMMARIZE_THRESHOLD


def get_turns_to_summarize(turns: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split turns into (old_turns_to_summarize, recent_turns_to_keep).

    Returns empty old_turns if the conversation is short enough.
    """
    if len(turns) <= SUMMARIZE_THRESHOLD:
        return [], turns

    split_point = len(turns) - KEEP_RECENT_TURNS
    return turns[:split_point], turns[split_point:]
