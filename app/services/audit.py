"""Audit log — structured, append-only, separate from application log (C.3).

Application logs (stdout / PIIRedactorFilter) are for operators debugging live
traffic. Audit events are for after-the-fact forensics: regulator / lawyer /
customer-support queries about "what happened to this user on that day".

Storage: a Redis Stream (healthai:audit) trimmed by MAXLEN. Short-term-only —
for regulatory retention you'd mirror the stream into something durable
(Postgres / S3) via a background consumer. That's out of scope here.

Hard rule: audit entries MUST NOT carry user message content, LLM answers, or
profile details. Store identifiers (user_id, conversation_id, request_id) and
metadata (intent category, risk level, rag usage, token counts, finish reason).
The audit entry is a *receipt* of an action, not the content of the action.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, cast

from ..config import settings
from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Approximate cap — the ~ flag tells Redis it can trim slightly more than
# MAXLEN for efficiency.
_AUDIT_STREAM_MAXLEN = 1_000_000


def _stream_key() -> str:
    return f"{settings.redis_prefix}:audit"


# Event types (extend as new auditable actions land).
EVENT_CHAT_ANSWER = "chat.answer"
EVENT_CHAT_REFUSAL = "chat.refusal"
EVENT_CHAT_INJECTION_BLOCKED = "chat.injection_blocked"
EVENT_CONVERSATION_DELETE = "conversation.delete"
# Triage events (D.3.a). Payloads carry identifiers + step metadata only —
# never raw user answers or the clinical summary text.
EVENT_TRIAGE_START = "triage.start"
EVENT_TRIAGE_STEP = "triage.step"
EVENT_TRIAGE_COMPLETE = "triage.complete"
EVENT_TRIAGE_RED_FLAG_EXIT = "triage.red_flag_exit"
EVENT_TRIAGE_ABANDONED = "triage.abandoned"
EVENT_TRIAGE_INJECTION_BLOCKED = "triage.injection_blocked"
# Sensitive-topic policy blocks (off-policy non-medical content: sex, profanity,
# recreational drugs, violence). Distinct from injection_blocked — different
# semantics, different forensic signal.
EVENT_CHAT_SENSITIVE_BLOCKED = "chat.sensitive_blocked"
EVENT_TRIAGE_SENSITIVE_BLOCKED = "triage.sensitive_blocked"
EVENT_ARTICLE_SENSITIVE_BLOCKED = "article.sensitive_blocked"
# Post-LLM screening: model output contained a banned token despite the input
# passing the pre-LLM gate. Distinct event so SREs can spot prompt-leak drift
# (it should be near-zero — non-zero means the LLM is paraphrasing past the
# prompt's "never write this word" rule and we may need to harden the prompt).
EVENT_CHAT_SENSITIVE_BLOCKED_POST = "chat.sensitive_blocked_post"


async def record_audit_event(
    event_type: str,
    user_id: str,
    conversation_id: str | None = None,
    request_id: str | None = None,
    **fields: Any,
) -> None:
    """Append a structured event to the audit stream. Never raises.

    Audit failures must not break the main flow — a missing audit entry is a
    regulatory-hygiene issue, not a request-failure one. We log at WARNING so
    ops notices if the stream ever goes unreachable for long.
    """
    entry: dict[str, str] = {
        "ts": f"{time.time():.6f}",
        "type": event_type,
        "user_id": user_id,
        "conversation_id": conversation_id or "",
        "request_id": request_id or "",
    }
    for key, value in fields.items():
        entry[key] = _serialize(value)

    try:
        r = get_redis()
        # redis-py types `xadd`'s fields dict as invariant `dict[<bytes|str|int|float>,
        # <bytes|str|int|float>]`; our `entry` is the equivalent `dict[str, str]` but
        # mypy refuses the implicit upcast. Cast `r` to `Any` to suppress.
        await cast(Any, r).xadd(
            _stream_key(),
            entry,
            maxlen=_AUDIT_STREAM_MAXLEN,
            approximate=True,
        )
    except Exception:
        logger.warning("Audit event %s not persisted (Redis error)", event_type, exc_info=True)


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
