"""Tests for OpenTelemetry tracing setup (B.3).

Two contracts:
1. Without OTEL_EXPORTER_OTLP_ENDPOINT, setup_tracing is a no-op and the
   pipeline still functions normally (no provider, no spans, no errors).
2. With an in-memory span exporter wired in by hand, exercising chat() emits
   the expected `intent.classify` / `rag.build` / `llm.generate` spans with
   the documented attributes.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.services.intent import IntentResult


def test_setup_tracing_is_noop_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    # Reset the module-level guard so this test isn't sensitive to import order.
    from app import tracing

    monkeypatch.setattr(tracing, "_INITIALIZED", False)

    assert tracing.setup_tracing("svc", "1.0") is False
    assert tracing._INITIALIZED is False


def test_setup_tracing_initializes_provider_when_endpoint_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    from app import tracing

    monkeypatch.setattr(tracing, "_INITIALIZED", False)

    # Patch the OTLP exporter so we don't try to open a real gRPC connection.
    with patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"):
        assert tracing.setup_tracing("svc", "1.0") is True
        assert tracing._INITIALIZED is True

    # Reset for downstream tests.
    monkeypatch.setattr(tracing, "_INITIALIZED", False)


def test_setup_tracing_idempotent(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    from app import tracing

    monkeypatch.setattr(tracing, "_INITIALIZED", True)
    # Second call short-circuits.
    assert tracing.setup_tracing("svc", "1.0") is True
    monkeypatch.setattr(tracing, "_INITIALIZED", False)


@pytest.fixture
def in_memory_exporter():
    """Wire an in-memory exporter to the global TracerProvider.

    Resets the global provider when the test ends so subsequent tests aren't
    affected.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Save and replace the global tracer provider.
    original_provider = trace._TRACER_PROVIDER  # noqa: SLF001
    trace._TRACER_PROVIDER = provider  # noqa: SLF001

    # Re-bind app.tracing.tracer to use the new provider.
    from app import tracing as tracing_module

    original_tracer = tracing_module.tracer
    tracing_module.tracer = trace.get_tracer(__name__)
    # Routers cached the tracer at import time too — refresh them.
    from app.routers import chat as chat_module

    original_chat_tracer = chat_module.tracer
    chat_module.tracer = trace.get_tracer(__name__)

    yield exporter

    tracing_module.tracer = original_tracer
    chat_module.tracer = original_chat_tracer
    trace._TRACER_PROVIDER = original_provider  # noqa: SLF001


async def test_chat_pipeline_emits_expected_spans(in_memory_exporter, mock_redis, mock_qdrant):
    mock_intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )

    with (
        patch("app.services.redis_client._redis", mock_redis),
        patch("app.services.redis_client.get_redis", return_value=mock_redis),
        patch("app.services.memory.get_redis", return_value=mock_redis),
        patch("app.services.vector_client._qdrant", mock_qdrant),
        patch("app.services.vector_client.get_qdrant", return_value=mock_qdrant),
        patch("app.services.vector_store.ensure_qdrant_collection", new_callable=AsyncMock),
        patch("app.routers.chat.classify_intent", new_callable=AsyncMock, return_value=mock_intent),
        patch("app.routers.chat.build_rag_context", new_callable=AsyncMock, return_value=("", [], None)),
        patch(
            "app.routers.chat.generate_health_answer",
            new_callable=AsyncMock,
            return_value="Stay hydrated.",
        ),
        patch("app.routers.chat.enforce_rate_limit", new_callable=AsyncMock),
    ):
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat",
                json={"message": "hi", "locale": "en"},
                headers={"X-Service-Token": "test-token", "X-User-Id": "trace-user"},
            )

    assert resp.status_code == 200

    span_names = {s.name for s in in_memory_exporter.get_finished_spans()}
    assert {"intent.classify", "rag.build", "llm.generate"}.issubset(
        span_names
    ), f"missing pipeline spans, got: {span_names}"

    # Spot-check attributes on the intent span.
    intent_span = next(s for s in in_memory_exporter.get_finished_spans() if s.name == "intent.classify")
    assert intent_span.attributes["intent.category"] == "general_health"
    assert intent_span.attributes["intent.risk_level"] == "low"

    # rag.build records chunk count even when there are zero hits.
    rag_span = next(s for s in in_memory_exporter.get_finished_spans() if s.name == "rag.build")
    assert rag_span.attributes["rag.chunks"] == 0

    # llm.generate records temperature.
    llm_span = next(s for s in in_memory_exporter.get_finished_spans() if s.name == "llm.generate")
    assert "llm.temperature" in llm_span.attributes
