from __future__ import annotations

from unittest.mock import Mock

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sherpa_mcp import telemetry


@pytest.fixture(autouse=True)
def fresh_telemetry(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """`configure` starts unconfigured and never replaces the global provider the span fixture installed."""
    monkeypatch.setattr(telemetry, "_provider", None)
    set_provider = Mock()
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", set_provider)
    return set_provider


def _processors(provider: object) -> list[object]:
    return list(provider._active_span_processor._span_processors)  # type: ignore[attr-defined]


class TestConfigure:
    def test_without_an_endpoint_there_is_a_provider_and_nothing_exports(self, fresh_telemetry: Mock) -> None:
        provider = telemetry.configure("sherpa-mcp", None)

        fresh_telemetry.assert_called_once_with(provider)
        assert _processors(provider) == []

    def test_with_an_endpoint_one_batch_processor_wraps_the_otlp_exporter(self) -> None:
        provider = telemetry.configure("sherpa-mcp", "http://127.0.0.1:4318")

        (processor,) = _processors(provider)
        assert isinstance(processor, BatchSpanProcessor)
        assert isinstance(processor.span_exporter, OTLPSpanExporter)

    def test_service_name_is_the_argument(self) -> None:
        provider = telemetry.configure("sherpa-other", None)

        assert provider.resource.attributes["service.name"] == "sherpa-other"

    def test_a_second_call_keeps_the_first_provider(self, fresh_telemetry: Mock) -> None:
        first = telemetry.configure("sherpa-mcp", None)

        second = telemetry.configure("another", "http://127.0.0.1:4318")

        assert second is first
        assert fresh_telemetry.call_count == 1
        assert _processors(second) == []
