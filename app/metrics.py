"""Prometheus metrics for health-ai-service.

Previously metrics lived in a per-process dict, so with gunicorn -w 4 each
scrape landed on one worker and saw only its slice of activity. This module
uses prometheus_client with multiprocess mode, so any worker can expose the
aggregate view for scraping.

Multiprocess mode requires PROMETHEUS_MULTIPROC_DIR to be set and writable
before any metric is instantiated. The Dockerfile sets /tmp/prom_multiproc
and owns it to appuser. For local dev we fall back to the system temp dir so
tests and uvicorn --reload work without extra config.

The legacy call-site API (metrics.record_request / record_intent /
record_openai_usage / record_rag_result) is preserved via _MetricsFacade so
routers and middleware don't need to change.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PROM_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if not _PROM_DIR:
    _PROM_DIR = str(Path(tempfile.gettempdir()) / "healthai_prom_multiproc")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = _PROM_DIR
Path(_PROM_DIR).mkdir(parents=True, exist_ok=True)

from prometheus_client import (  # noqa: E402  (must follow env setup)
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST  # noqa: E402

REQUESTS_TOTAL = Counter(
    "healthai_requests_total",
    "Total HTTP requests.",
    ["status", "path_tag"],
)

REQUEST_DURATION = Histogram(
    "healthai_request_duration_seconds",
    "HTTP request duration.",
    ["path_tag"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

INTENT_TOTAL = Counter(
    "healthai_intent_total",
    "Intent classification distribution.",
    ["category", "risk_level"],
)

OPENAI_TOKENS = Counter(
    "healthai_openai_tokens_total",
    "OpenAI tokens consumed.",
    ["type", "call_type"],  # type=prompt|completion, call_type=intent|generate|summarize
)

RAG_REQUESTS = Counter(
    "healthai_rag_requests_total",
    "RAG retrieval attempts.",
    ["hit"],  # "true" | "false"
)

INTENT_PATH = Counter(
    "healthai_intent_path_total",
    "Which code path answered an intent classification.",
    ["path"],  # "fast" | "cache" | "llm"
)

CIRCUIT_BREAKER_STATE = Gauge(
    "healthai_circuit_breaker_state",
    "Circuit breaker state: 0=closed, 1=half_open, 2=open.",
    ["name"],
    multiprocess_mode="max",  # if any worker observed open, report open
)

ACTIVE_CONVERSATIONS = Gauge(
    "healthai_active_conversations",
    "Approximate count of live conversation keys in Redis.",
    multiprocess_mode="mostrecent",
)

QDRANT_COLLECTION_SIZE = Gauge(
    "healthai_qdrant_collection_size",
    "Points in the active Qdrant RAG collection.",
    multiprocess_mode="mostrecent",
)

_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


class _MetricsFacade:
    """Legacy call-site API so routers / middleware don't have to change."""

    def record_request(self, status: int, duration_ms: float, path_tag: str = "unknown") -> None:
        REQUESTS_TOTAL.labels(status=str(status), path_tag=path_tag).inc()
        REQUEST_DURATION.labels(path_tag=path_tag).observe(duration_ms / 1000.0)

    def record_intent(self, category: str, risk_level: str = "unknown") -> None:
        INTENT_TOTAL.labels(category=category, risk_level=risk_level).inc()

    def record_openai_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        call_type: str = "generate",
    ) -> None:
        # Coerce defensively: mocked OpenAI response objects from tests sometimes
        # hand us MagicMocks, and prometheus_client's Counter.inc does a numeric
        # compare that would TypeError otherwise.
        try:
            p = int(prompt_tokens or 0)
            c = int(completion_tokens or 0)
        except (TypeError, ValueError):
            return
        OPENAI_TOKENS.labels(type="prompt", call_type=call_type).inc(p)
        OPENAI_TOKENS.labels(type="completion", call_type=call_type).inc(c)

    def record_rag_result(self, hit: bool) -> None:
        RAG_REQUESTS.labels(hit="true" if hit else "false").inc()

    def record_intent_path(self, path: str) -> None:
        """Which intent classifier path answered: fast (embeddings), cache (Redis), llm (OpenAI)."""
        INTENT_PATH.labels(path=path).inc()

    def record_error(self) -> None:
        # Errors are already captured by record_request's 4xx/5xx buckets;
        # kept here so the old call sites (if any) stay valid.
        REQUESTS_TOTAL.labels(status="500", path_tag="unknown").inc()

    def set_circuit_breaker_state(self, name: str, state: str) -> None:
        CIRCUIT_BREAKER_STATE.labels(name=name).set(_STATE_VALUE.get(state, 0))

    def set_active_conversations(self, count: int) -> None:
        ACTIVE_CONVERSATIONS.set(count)

    def set_qdrant_collection_size(self, count: int) -> None:
        QDRANT_COLLECTION_SIZE.set(count)


metrics = _MetricsFacade()


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics HTTP response.

    In multiprocess mode every worker writes to files under PROMETHEUS_MULTIPROC_DIR;
    a dedicated MultiProcessCollector aggregates them for the scrape response.
    """
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry), CONTENT_TYPE_LATEST
