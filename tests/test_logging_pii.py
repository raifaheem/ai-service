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


def _make_record_with_extra(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_nested_user_message_in_dict_is_redacted():
    """Common shape: extra={"context": {"user_message": "..."}} must not leak."""
    record = _make_record_with_extra(context={"user_message": "I have severe chest pain", "intent": "symptom_check"})
    assert PIIRedactorFilter().filter(record) is True
    assert record.context["user_message"] == "<REDACTED>"
    assert record.context["intent"] == "symptom_check"


def test_nested_user_message_in_list_of_dicts_is_redacted():
    record = _make_record_with_extra(
        items=[
            {"user_message": "headache for 3 days"},
            {"user_message": "back pain"},
            {"safe": "ok"},
        ]
    )
    assert PIIRedactorFilter().filter(record) is True
    assert record.items[0]["user_message"] == "<REDACTED>"
    assert record.items[1]["user_message"] == "<REDACTED>"
    assert record.items[2]["safe"] == "ok"


def test_redaction_respects_max_depth():
    """Beyond _MAX_DEPTH levels, the filter stops descending."""
    deep = {"l1": {"l2": {"l3": {"l4": {"user_message": "leaks past depth cap"}}}}}
    record = _make_record_with_extra(payload=deep)
    PIIRedactorFilter().filter(record)
    # MAX_DEPTH=3 => l4 is depth 4, never visited; user_message at l4 stays unredacted.
    assert record.payload["l1"]["l2"]["l3"]["l4"]["user_message"] == "leaks past depth cap"


def test_redaction_at_max_depth_does_redact():
    """Boundary check: a PII key sitting exactly at depth 3 is still redacted."""
    record = _make_record_with_extra(payload={"l1": {"l2": {"user_message": "should be redacted"}}})
    PIIRedactorFilter().filter(record)
    assert record.payload["l1"]["l2"]["user_message"] == "<REDACTED>"


def test_redaction_handles_self_referential_dict():
    """A circular structure must not blow up the filter."""
    cycle: dict = {"user_message": "leak"}
    cycle["self"] = cycle
    record = _make_record_with_extra(payload={"outer": cycle})
    # Must not raise (RecursionError, etc.)
    assert PIIRedactorFilter().filter(record) is True
    assert cycle["user_message"] == "<REDACTED>"


def test_redaction_does_not_alter_primitive_extras():
    """Non-container extras (str, int, float, bool, None) must pass through untouched."""
    record = _make_record_with_extra(
        duration_ms=42.5,
        intent_category="symptom_check",
        ok=True,
        nothing=None,
    )
    PIIRedactorFilter().filter(record)
    assert record.duration_ms == 42.5
    assert record.intent_category == "symptom_check"
    assert record.ok is True
    assert record.nothing is None


def test_redaction_respects_node_budget():
    """Hostile records with thousands of keys are truncated by the node budget."""
    # Build a wide dict that overflows _MAX_NODES (1000).
    wide = {f"k{i}": {"user_message": f"leak{i}"} for i in range(2000)}
    record = _make_record_with_extra(payload=wide)
    PIIRedactorFilter().filter(record)
    # Some entries get redacted (within budget), some don't — but the filter doesn't crash.
    redacted_count = sum(1 for v in wide.values() if v["user_message"] == "<REDACTED>")
    assert redacted_count > 0  # budget allowed at least one redaction
    # The exact count depends on traversal order; the contract is "no crash, partial coverage OK".
