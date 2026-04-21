"""Tests for prometheus_client-backed metrics facade (B.2).

The old per-attribute assertions (m.requests_total == 2) are gone — state lives
in prometheus_client collectors. These tests exercise the facade by calling it
and inspecting the underlying Counter/Histogram samples.
"""

from app.metrics import (
    INTENT_TOTAL,
    OPENAI_TOKENS,
    RAG_REQUESTS,
    REQUEST_DURATION,
    REQUESTS_TOTAL,
    metrics,
    render_metrics,
)


def _counter_value(counter, **labels) -> float:
    """Read the current value of a labelled Counter sample."""
    return counter.labels(**labels)._value.get()


def _histogram_sum(histogram, **labels) -> float:
    return histogram.labels(**labels)._sum.get()


class TestRecordRequest:
    def test_increments_status_path_counter(self):
        before = _counter_value(REQUESTS_TOTAL, status="200", path_tag="/v1/chat")
        metrics.record_request(200, 50.0, path_tag="/v1/chat")
        metrics.record_request(200, 30.0, path_tag="/v1/chat")
        assert _counter_value(REQUESTS_TOTAL, status="200", path_tag="/v1/chat") == before + 2

    def test_tracks_different_statuses_separately(self):
        before_200 = _counter_value(REQUESTS_TOTAL, status="200", path_tag="/health")
        before_500 = _counter_value(REQUESTS_TOTAL, status="500", path_tag="/health")

        metrics.record_request(200, 1.0, path_tag="/health")
        metrics.record_request(500, 1.0, path_tag="/health")

        assert _counter_value(REQUESTS_TOTAL, status="200", path_tag="/health") == before_200 + 1
        assert _counter_value(REQUESTS_TOTAL, status="500", path_tag="/health") == before_500 + 1

    def test_records_duration_in_histogram(self):
        before_sum = _histogram_sum(REQUEST_DURATION, path_tag="/v1/chat")
        metrics.record_request(200, 150.0, path_tag="/v1/chat")
        # 150ms → 0.15s; allow tiny float drift from repeated observations in the test run.
        assert abs((_histogram_sum(REQUEST_DURATION, path_tag="/v1/chat") - before_sum) - 0.15) < 1e-6


class TestRecordIntent:
    def test_labels_by_category_and_risk(self):
        before = _counter_value(INTENT_TOTAL, category="symptom_check", risk_level="medium")
        metrics.record_intent("symptom_check", risk_level="medium")
        assert _counter_value(INTENT_TOTAL, category="symptom_check", risk_level="medium") == before + 1

    def test_default_risk_level(self):
        before = _counter_value(INTENT_TOTAL, category="lifestyle", risk_level="unknown")
        metrics.record_intent("lifestyle")
        assert _counter_value(INTENT_TOTAL, category="lifestyle", risk_level="unknown") == before + 1


class TestRecordOpenaiUsage:
    def test_tokens_split_by_type_and_call(self):
        p_before = _counter_value(OPENAI_TOKENS, type="prompt", call_type="generate")
        c_before = _counter_value(OPENAI_TOKENS, type="completion", call_type="generate")

        metrics.record_openai_usage(100, 50, call_type="generate")

        assert _counter_value(OPENAI_TOKENS, type="prompt", call_type="generate") == p_before + 100
        assert _counter_value(OPENAI_TOKENS, type="completion", call_type="generate") == c_before + 50

    def test_call_types_tracked_independently(self):
        intent_before = _counter_value(OPENAI_TOKENS, type="prompt", call_type="intent")
        summarize_before = _counter_value(OPENAI_TOKENS, type="prompt", call_type="summarize")

        metrics.record_openai_usage(20, 10, call_type="intent")
        metrics.record_openai_usage(30, 15, call_type="summarize")

        assert _counter_value(OPENAI_TOKENS, type="prompt", call_type="intent") == intent_before + 20
        assert _counter_value(OPENAI_TOKENS, type="prompt", call_type="summarize") == summarize_before + 30


class TestRecordRagResult:
    def test_tracks_hits_and_misses_separately(self):
        hit_before = _counter_value(RAG_REQUESTS, hit="true")
        miss_before = _counter_value(RAG_REQUESTS, hit="false")

        metrics.record_rag_result(True)
        metrics.record_rag_result(True)
        metrics.record_rag_result(False)

        assert _counter_value(RAG_REQUESTS, hit="true") == hit_before + 2
        assert _counter_value(RAG_REQUESTS, hit="false") == miss_before + 1


class TestRenderMetrics:
    def test_returns_prometheus_text_format(self):
        metrics.record_request(200, 10.0, path_tag="/health")
        metrics.record_intent("general_health", risk_level="low")
        metrics.record_openai_usage(10, 5, call_type="generate")
        metrics.record_rag_result(True)

        body, content_type = render_metrics()
        assert content_type.startswith("text/plain")
        assert b"healthai_requests_total" in body
        assert b"healthai_intent_total" in body
        assert b"healthai_openai_tokens_total" in body
        assert b"healthai_rag_requests_total" in body

    def test_content_type_is_prometheus_v0_0_4(self):
        _, content_type = render_metrics()
        assert "version=0.0.4" in content_type
