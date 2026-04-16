"""Unit tests for scripts/verify_knowledge_base.py."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts import verify_knowledge_base as verify
from scripts._common import ServiceClient


def _client(*, metrics=None, rag_stats=None, rag_search=None) -> MagicMock:
    c = MagicMock(spec=ServiceClient)
    c.metrics.return_value = metrics if metrics is not None else {"qdrant_collection_size": 120}
    c.rag_stats.return_value = rag_stats if rag_stats is not None else {
        "sources_by_language": {"ru": 10, "en": 10, "kk": 10},
    }
    c.rag_search.return_value = rag_search if rag_search is not None else {
        "results": [{"score": 0.72}],
    }
    return c


# --------------- check_total_chunks ---------------


class TestCheckTotalChunks:
    def test_passes_when_nonzero(self):
        c = _client(metrics={"qdrant_collection_size": 5})
        check = verify.check_total_chunks(c)
        assert check.ok is True
        assert "5" in check.detail

    def test_fails_when_zero(self):
        c = _client(metrics={"qdrant_collection_size": 0})
        check = verify.check_total_chunks(c)
        assert check.ok is False

    def test_fails_when_missing(self):
        c = _client(metrics={})
        check = verify.check_total_chunks(c)
        assert check.ok is False

    def test_fails_on_http_error(self):
        c = _client()
        req = httpx.Request("GET", "/metrics")
        c.metrics.side_effect = httpx.ConnectError("boom", request=req)
        check = verify.check_total_chunks(c)
        assert check.ok is False
        assert "could not reach" in check.detail


# --------------- check_language_coverage ---------------


class TestCheckLanguageCoverage:
    def test_passes_when_all_meet_minimum(self):
        c = _client(rag_stats={"sources_by_language": {"ru": 10, "en": 12, "kk": 11}})
        check = verify.check_language_coverage(
            c, required_languages=("ru", "en", "kk"), min_sources_per_lang=10,
        )
        assert check.ok is True

    def test_fails_when_one_below_minimum(self):
        c = _client(rag_stats={"sources_by_language": {"ru": 10, "en": 9, "kk": 10}})
        check = verify.check_language_coverage(
            c, required_languages=("ru", "en", "kk"), min_sources_per_lang=10,
        )
        assert check.ok is False
        assert "en has 9" in check.detail

    def test_fails_when_language_missing_entirely(self):
        c = _client(rag_stats={"sources_by_language": {"ru": 10, "en": 10}})
        check = verify.check_language_coverage(
            c, required_languages=("ru", "en", "kk"), min_sources_per_lang=10,
        )
        assert check.ok is False
        assert "kk has 0" in check.detail

    def test_surfaces_dev_routes_hint_on_http_error(self):
        c = _client()
        req = httpx.Request("GET", "/v1/rag/stats")
        c.rag_stats.side_effect = httpx.ConnectError("boom", request=req)
        check = verify.check_language_coverage(
            c, required_languages=("ru",), min_sources_per_lang=1,
        )
        assert check.ok is False
        assert "ENABLE_DEV_ROUTES" in check.detail


# --------------- check_query_relevance ---------------


class TestCheckQueryRelevance:
    def test_passes_when_ratio_met(self):
        c = _client(rag_search={"results": [{"score": 0.8}]})
        queries = [{"query": "q1", "language": "ru"}, {"query": "q2", "language": "en"}]
        check = verify.check_query_relevance(
            c, queries=queries, score_threshold=0.35, pass_ratio=0.5,
        )
        assert check.ok is True

    def test_fails_when_scores_below_threshold(self):
        c = _client(rag_search={"results": [{"score": 0.1}]})
        queries = [{"query": "q1", "language": "ru"}] * 2
        check = verify.check_query_relevance(
            c, queries=queries, score_threshold=0.35, pass_ratio=0.5,
        )
        assert check.ok is False
        assert "0/2" in check.detail

    def test_handles_no_results(self):
        c = _client(rag_search={"results": []})
        queries = [{"query": "q1", "language": "ru"}]
        check = verify.check_query_relevance(
            c, queries=queries, score_threshold=0.35, pass_ratio=1.0,
        )
        assert check.ok is False

    def test_treats_http_error_as_miss(self):
        c = _client()
        req = httpx.Request("POST", "/v1/rag/search")
        c.rag_search.side_effect = httpx.ConnectError("boom", request=req)
        queries = [{"query": "q", "language": "ru"}]
        check = verify.check_query_relevance(
            c, queries=queries, score_threshold=0.35, pass_ratio=0.5,
        )
        assert check.ok is False

    def test_empty_queries_fails(self):
        c = _client()
        check = verify.check_query_relevance(
            c, queries=[], score_threshold=0.35, pass_ratio=0.5,
        )
        assert check.ok is False


# --------------- load_queries ---------------


class TestLoadQueries:
    def test_returns_defaults_when_none(self):
        queries = verify.load_queries(None)
        assert len(queries) > 0
        assert all("query" in q for q in queries)

    def test_reads_custom_file(self, tmp_path):
        import json
        path = tmp_path / "q.json"
        path.write_text(json.dumps([{"query": "custom", "language": "ru"}]), encoding="utf-8")
        queries = verify.load_queries(path)
        assert queries[0]["query"] == "custom"

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(SystemExit, match="not found"):
            verify.load_queries(tmp_path / "nope.json")

    def test_rejects_non_list(self, tmp_path):
        path = tmp_path / "q.json"
        path.write_text('{"not": "a list"}', encoding="utf-8")
        with pytest.raises(SystemExit, match="must contain a JSON list"):
            verify.load_queries(path)


# --------------- run + main ---------------


class TestRun:
    def test_green_run(self):
        # Patch ServiceClient constructor so we control the instance end-to-end
        with patch("scripts.verify_knowledge_base.ServiceClient") as ctor, \
             patch("scripts.verify_knowledge_base.load_service_token", return_value="tok"):
            instance = _client()
            ctor.return_value.__enter__.return_value = instance

            report = verify.run(
                base_url="http://x",
                min_sources_per_lang=10,
                required_languages=("ru", "en", "kk"),
                score_threshold=0.35,
                pass_ratio=0.5,
                queries=[{"query": "q", "language": "ru"}],
            )

        assert report.all_ok is True
        assert len(report.checks) == 3

    def test_report_flags_failures(self):
        with patch("scripts.verify_knowledge_base.ServiceClient") as ctor, \
             patch("scripts.verify_knowledge_base.load_service_token", return_value="tok"):
            instance = _client(metrics={"qdrant_collection_size": 0})
            ctor.return_value.__enter__.return_value = instance

            report = verify.run(
                base_url="http://x",
                min_sources_per_lang=10,
                required_languages=("ru",),
                score_threshold=0.35,
                pass_ratio=0.5,
                queries=[{"query": "q", "language": "ru"}],
            )

        assert report.all_ok is False


class TestMain:
    def test_main_exits_zero_when_all_green(self):
        with patch("scripts.verify_knowledge_base.ServiceClient") as ctor, \
             patch("scripts.verify_knowledge_base.load_service_token", return_value="tok"):
            ctor.return_value.__enter__.return_value = _client()

            rc = verify.main(["--base-url", "http://x", "--min-sources-per-lang", "1"])
        assert rc == 0

    def test_main_exits_nonzero_on_failure(self):
        with patch("scripts.verify_knowledge_base.ServiceClient") as ctor, \
             patch("scripts.verify_knowledge_base.load_service_token", return_value="tok"):
            ctor.return_value.__enter__.return_value = _client(metrics={"qdrant_collection_size": 0})

            rc = verify.main(["--base-url", "http://x"])
        assert rc == 1
