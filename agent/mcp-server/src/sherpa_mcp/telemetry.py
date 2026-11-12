"""OpenTelemetry setup for the server (agent spec §6): a tracer provider in every process, an OTLP/HTTP exporter only
when an endpoint is set. There is no console exporter: stdout carries the stdio JSON-RPC stream."""

from __future__ import annotations

from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME_KEY: Final = "service.name"
TRACES_PATH: Final = "/v1/traces"  # OTLP/HTTP

_provider: TracerProvider | None = None


def configure(service_name: str, otlp_endpoint: str | None) -> TracerProvider:
    """Install the global tracer provider once; a second call returns the first provider unchanged."""
    global _provider
    if _provider is not None:
        return _provider

    provider = TracerProvider(resource=Resource({SERVICE_NAME_KEY: service_name}))
    if otlp_endpoint:
        # Explicit, so the traces go where this process was told and not where another OTEL_* variable points.
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}{TRACES_PATH}")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider
    return provider
