import io
import json
import logging

from app.context import set_request_id, set_conversation_id, set_user_id
from app.logging_config import ContextFilter, setup_logging


def _make_record(message="test message"):
    """Create a LogRecord for testing."""
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestContextFilter:
    def test_injects_defaults(self):
        flt = ContextFilter()
        record = _make_record()
        result = flt.filter(record)
        assert result is True
        assert hasattr(record, "request_id")
        assert hasattr(record, "conversation_id")
        assert hasattr(record, "user_id")

    def test_injects_set_values(self):
        set_request_id("test-req-id")
        set_conversation_id("test-conv-id")
        set_user_id("test-user-id")

        flt = ContextFilter()
        record = _make_record()
        flt.filter(record)

        assert record.request_id == "test-req-id"
        assert record.conversation_id == "test-conv-id"
        assert record.user_id == "test-user-id"

        # Cleanup
        set_request_id("-")
        set_conversation_id("-")
        set_user_id("-")


class TestSetupLogging:
    def _capture_output(self, log_format: str, log_level: str = "INFO") -> tuple[logging.Logger, io.StringIO]:
        """Setup logging with a captured stream and return (logger, stream)."""
        setup_logging(log_level, log_format)
        root = logging.getLogger()

        # Replace handler stream with our capture buffer
        # The handler already has the ContextFilter from setup_logging
        stream = io.StringIO()
        for handler in root.handlers:
            handler.stream = stream
            handler.flush()
        return root, stream

    def test_json_format_produces_json(self):
        root, stream = self._capture_output("json")
        test_logger = logging.getLogger("test.json")
        test_logger.info("hello json")

        output = stream.getvalue()
        assert output.strip(), "No log output"
        data = json.loads(output.strip())
        assert data["message"] == "hello json"
        assert "timestamp" in data
        assert "level" in data

    def test_text_format_produces_readable_output(self):
        root, stream = self._capture_output("text")
        test_logger = logging.getLogger("test.text")
        test_logger.info("hello text")

        output = stream.getvalue()
        assert "hello text" in output
        assert "INFO" in output

    def test_extra_fields_in_json(self):
        root, stream = self._capture_output("json")
        test_logger = logging.getLogger("test.extra")
        test_logger.info("with extras", extra={"custom_field": "custom_value", "tokens": 42})

        output = stream.getvalue()
        data = json.loads(output.strip())
        assert data["custom_field"] == "custom_value"
        assert data["tokens"] == 42

    def test_log_level_respected(self):
        root, stream = self._capture_output("text", "WARNING")
        test_logger = logging.getLogger("test.level")
        test_logger.info("should not appear")
        test_logger.warning("should appear")

        output = stream.getvalue()
        assert "should not appear" not in output
        assert "should appear" in output

    def test_context_vars_in_json_output(self):
        set_request_id("req-abc")
        root, stream = self._capture_output("json")
        test_logger = logging.getLogger("test.ctx")
        test_logger.info("context test")

        output = stream.getvalue()
        data = json.loads(output.strip())
        assert data["request_id"] == "req-abc"

        set_request_id("-")
