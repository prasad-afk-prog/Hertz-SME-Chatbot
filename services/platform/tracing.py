"""OpenTelemetry tracing baseline (M15 §5).

The seam is always wired into ``create_app``; it stays a no-op unless
``HFB_OTEL_ENABLED=true`` AND the OpenTelemetry libraries are installed, so the
skeleton carries no hard OTel dependency yet. Turn it on with:

    pip install opentelemetry-sdk opentelemetry-exporter-otlp \\
                opentelemetry-instrumentation-fastapi
    export HFB_OTEL_ENABLED=true HFB_OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .config import Settings
from .logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger("platform.tracing")


def configure_tracing(app: FastAPI, settings: Settings, service_name: str) -> bool:
    """Return True if tracing was actually enabled, else False."""
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning("otel_enabled but OpenTelemetry libraries are not installed; tracing disabled")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _log.info("tracing_enabled", extra={"extra_fields": {"service": service_name}})
    return True
