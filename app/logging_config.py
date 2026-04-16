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


def setup_logging(log_level: str = "INFO", log_format: str = "text") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicate output
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

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
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Suppress noisy uvicorn access logs (we handle request logging in middleware)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
