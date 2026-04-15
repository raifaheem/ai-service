import time

from app.metrics import Metrics


def _fresh_metrics():
    return Metrics()


class TestRecordRequest:
    def test_increments_total(self):
        m = _fresh_metrics()
        m.record_request(200, 50.0)
        m.record_request(200, 30.0)
        assert m.requests_total == 2

    def test_tracks_by_status(self):
        m = _fresh_metrics()
        m.record_request(200, 10.0)
        m.record_request(200, 20.0)
        m.record_request(404, 5.0)
        m.record_request(500, 100.0)
        assert m.requests_by_status[200] == 2
        assert m.requests_by_status[404] == 1
        assert m.requests_by_status[500] == 1

    def test_records_response_time(self):
        m = _fresh_metrics()
        m.record_request(200, 100.0)
        m.record_request(200, 200.0)
        assert len(m.response_times) == 2

    def test_4xx_5xx_records_error_timestamp(self):
        m = _fresh_metrics()
        m.record_request(200, 10.0)
        m.record_request(400, 10.0)
        m.record_request(500, 10.0)
        assert len(m.errors_timestamps) == 2


class TestRecordIntent:
    def test_tracks_categories(self):
        m = _fresh_metrics()
        m.record_intent("symptom_check")
        m.record_intent("symptom_check")
        m.record_intent("lifestyle")
        assert m.requests_by_intent["symptom_check"] == 2
        assert m.requests_by_intent["lifestyle"] == 1


class TestRecordOpenaiUsage:
    def test_accumulates_tokens(self):
        m = _fresh_metrics()
        m.record_openai_usage(100, 50)
        m.record_openai_usage(200, 100)
        assert m.openai_tokens_prompt == 300
        assert m.openai_tokens_completion == 150


class TestRecordRagResult:
    def test_tracks_hits_and_misses(self):
        m = _fresh_metrics()
        m.record_rag_result(True)
        m.record_rag_result(True)
        m.record_rag_result(False)
        assert m.rag_requests == 3
        assert m.rag_hits == 2


class TestRecordError:
    def test_records_error_timestamp(self):
        m = _fresh_metrics()
        m.record_error()
        assert len(m.errors_timestamps) == 1


class TestSnapshot:
    def test_returns_expected_keys(self):
        m = _fresh_metrics()
        snap = m.snapshot()
        expected_keys = {
            "requests_total",
            "requests_by_status",
            "requests_by_intent",
            "avg_response_time_ms",
            "openai_tokens_prompt",
            "openai_tokens_completion",
            "openai_tokens_total",
            "rag_hit_rate",
            "rag_requests",
            "rag_hits",
            "error_rate_1h",
            "errors_in_last_hour",
        }
        assert expected_keys == set(snap.keys())

    def test_avg_response_time(self):
        m = _fresh_metrics()
        m.record_request(200, 100.0)
        m.record_request(200, 200.0)
        snap = m.snapshot()
        assert snap["avg_response_time_ms"] == 150.0

    def test_rag_hit_rate(self):
        m = _fresh_metrics()
        m.record_rag_result(True)
        m.record_rag_result(False)
        snap = m.snapshot()
        assert snap["rag_hit_rate"] == 0.5

    def test_rag_hit_rate_zero_requests(self):
        m = _fresh_metrics()
        snap = m.snapshot()
        assert snap["rag_hit_rate"] == 0.0

    def test_openai_tokens_total(self):
        m = _fresh_metrics()
        m.record_openai_usage(100, 50)
        snap = m.snapshot()
        assert snap["openai_tokens_total"] == 150

    def test_error_rate_1h(self):
        m = _fresh_metrics()
        m.record_request(200, 10.0)
        m.record_request(500, 10.0)
        snap = m.snapshot()
        assert snap["errors_in_last_hour"] == 1
        assert snap["error_rate_1h"] > 0

    def test_error_rate_excludes_old_errors(self):
        m = _fresh_metrics()
        m.record_request(200, 10.0)
        # Manually inject an old error timestamp (>1 hour ago)
        m.errors_timestamps.append(time.time() - 7200)
        snap = m.snapshot()
        assert snap["errors_in_last_hour"] == 0

    def test_empty_metrics(self):
        m = _fresh_metrics()
        snap = m.snapshot()
        assert snap["requests_total"] == 0
        assert snap["avg_response_time_ms"] == 0.0
        assert snap["openai_tokens_total"] == 0
