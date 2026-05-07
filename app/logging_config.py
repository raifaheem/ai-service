import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from .context import get_conversation_id, get_request_id, get_user_id


class ContextFilter(logging.Filter):
    """Injects request_id, conversation_id, user_id from contextvars into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.conversation_id = get_conversation_id()
        record.user_id = get_user_id()
        return True


class PIIRedactorFilter(logging.Filter):
    """Redact known PII keys from log record extras.

    Medical conversations routinely include user-supplied symptoms, conditions,
    and profile info. None of that should leak into application logs — the right
    substrate for debugging medical content is an audit stream (see app/services/audit.py),
    not stdout. This filter catches cases where a caller accidentally logs via
    `extra={"user_message": ...}` — or via a nested container like
    `extra={"context": {"user_message": ...}}` — by replacing the value with
    <REDACTED> before the formatter runs.

    Walks dicts/lists/tuples up to `_MAX_DEPTH` levels and `_MAX_NODES` total
    nodes — the cap protects against runaway traversal on hostile or
    accidentally circular structures.

    The formatter still has access to contextual ids (request_id, conversation_id,
    user_id) — those are not PII in the medical sense.
    """

    _REDACT_KEYS = frozenset(
        {
            "user_message",
            "message_content",
            "turn_content",
            "profile_text",
            "raw_answer",
            "answer_text",
        }
    )
    _REDACTED = "<REDACTED>"
    _MAX_DEPTH = 3
    _MAX_NODES = 1000

    def filter(self, record: logging.LogRecord) -> bool:
        seen: set[int] = set()
        budget = [self._MAX_NODES]
        for key in list(record.__dict__.keys()):
            if key in self._REDACT_KEYS:
                record.__dict__[key] = self._REDACTED
                continue
            value = record.__dict__[key]
            if isinstance(value, dict | list | tuple):
                record.__dict__[key] = self._redact_value(value, depth=1, seen=seen, budget=budget)
        return True

    def _redact_value(self, value, depth: int, seen: set[int], budget: list[int]):
        if budget[0] <= 0 or depth > self._MAX_DEPTH:
            return value
        budget[0] -= 1

        if isinstance(value, dict):
            ident = id(value)
            if ident in seen:
                return value
            seen.add(ident)
            for k in list(value.keys()):
                if k in self._REDACT_KEYS:
                    value[k] = self._REDACTED
                elif isinstance(value[k], dict | list | tuple):
                    value[k] = self._redact_value(value[k], depth + 1, seen, budget)
            return value

        if isinstance(value, list):
            ident = id(value)
            if ident in seen:
                return value
            seen.add(ident)
            for i, item in enumerate(value):
                if isinstance(item, dict | list | tuple):
                    value[i] = self._redact_value(item, depth + 1, seen, budget)
            return value

        if isinstance(value, tuple):
            return tuple(
                self._redact_value(item, depth + 1, seen, budget) if isinstance(item, dict | list | tuple) else item
                for item in value
            )

        return value


def setup_logging(log_level: str = "INFO", log_format: str = "text") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicate output
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.addFilter(ContextFilter())
    # Order matters: redaction happens after context injection so ids are still
    # present and only payload fields get replaced.
    handler.addFilter(PIIRedactorFilter())
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Suppress noisy uvicorn access logs (we handle request logging in middleware)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
