"""OpenTelemetry tracing setup (B.3).

The 11-step chat pipeline is tedious to debug from log lines alone — even with
request_id, the timing of each phase is split across separate records. With
OpenTelemetry, each phase becomes a span and Jaeger/Tempo can render the full
tree for a single request.

Activation is opt-in via the standard OTel env var:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

When that var is unset, setup_tracing returns immediately and the pipeline runs
without instrumentation overhead. The `tracer` exported below is always safe to
call — when no provider is configured, span creation is a near-no-op.

Service name and version flow into the resource so each span is identifiable
across multiple deployments.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace

logger = logging.getLogger(__name__)

# Module-level tracer — safe to import at any point, even before setup_tracing()
# runs. Pre-setup spans hit the default no-op provider.
tracer = trace.get_tracer(__name__)

_INITIALIZED = False


def setup_tracing(service_name: str, service_version: str) -> bool:
    """Configure the global TracerProvider if OTEL_EXPORTER_OTLP_ENDPOINT is set.

    Returns True if tracing was set up, False otherwise. Safe to call more than
    once — second and later calls are no-ops.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing disabled")
        return False

    # Imports deferred so that test environments without the OTLP exporter
    # available aren't forced to load it just to import this module.
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
        }
    )
    provider = TracerProvider(resource=resource)

    insecure = os.environ.get("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() != "false"
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _INITIALIZED = True
    logger.info(
        "OpenTelemetry tracing initialized: service=%s version=%s endpoint=%s",
        service_name,
        service_version,
        endpoint,
    )
    return True


def instrument_app(app) -> None:
    """Apply auto-instrumentation to FastAPI + Redis + HTTPX.

    Called from main.py once the FastAPI instance exists. Safe to call when
    setup_tracing returned False — the instrumentors just attach to a no-op
    provider and never emit spans.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
