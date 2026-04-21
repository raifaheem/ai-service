"""Tests for PII redaction in log records (B.5).

These are preventative: no current log statement passes user_message via extra,
but the filter is here so a future accidental leak still hits the guard.
"""

import io
import logging

from app.logging_config import PIIRedactorFilter


def _logger_with_redactor() -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(PIIRedactorFilter())
    # Use %(user_message)s so the test can detect leaks via the rendered output.
    handler.setFormatter(logging.Formatter("%(message)s | %(user_message)s"))
    logger = logging.getLogger(f"test-{id(buf)}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, buf


def test_user_message_is_redacted_when_logged_via_extra():
    logger, buf = _logger_with_redactor()
    logger.info("ignored", extra={"user_message": "I have a severe headache"})

    out = buf.getvalue()
    assert "severe headache" not in out
    assert "<REDACTED>" in out


def test_multiple_pii_keys_are_all_redacted():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(PIIRedactorFilter())
    handler.setFormatter(
        logging.Formatter("%(message)s | msg=%(user_message)s prof=%(profile_text)s turn=%(turn_content)s")
    )
    logger = logging.getLogger("test-multi")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.info(
        "record",
        extra={
            "user_message": "chest pain",
            "profile_text": "age 45 diabetes",
            "turn_content": "I've been feeling dizzy",
        },
    )

    out = buf.getvalue()
    assert "chest pain" not in out
    assert "diabetes" not in out
    assert "dizzy" not in out
    # Each PII slot now carries the redacted marker.
    assert out.count("<REDACTED>") == 3


def test_non_pii_fields_are_untouched():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(PIIRedactorFilter())
    handler.setFormatter(logging.Formatter("%(message)s | dur=%(duration_ms)s cat=%(intent_category)s"))
    logger = logging.getLogger("test-safe")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.info(
        "record",
        extra={"duration_ms": 42.5, "intent_category": "symptom_check"},
    )

    out = buf.getvalue()
    assert "42.5" in out
    assert "symptom_check" in out
    assert "<REDACTED>" not in out


def test_redactor_filter_always_passes_the_record():
    """filter() must return True so the record still gets emitted, just redacted."""
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    record.user_message = "secret"
    assert PIIRedactorFilter().filter(record) is True
    assert record.user_message == "<REDACTED>"
