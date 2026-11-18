from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sherpa_mcp import telemetry

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh_telemetry(monkeypatch: pytest.MonkeyPatch) -> Iterator[Mock]:
    """`configure` starts unconfigured and never replaces the global provider the span fixture installed."""
    monkeypatch.setattr(telemetry, "_provider", None)
    set_provider = Mock()
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", set_provider)
    yield set_provider
    if telemetry._provider is not None:
        telemetry._provider.shutdown()  # stops the batch processor's thread


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

    @pytest.mark.parametrize("endpoint", ["http://127.0.0.1:4318", "http://127.0.0.1:4318/"])
    def test_the_exporter_posts_to_the_given_endpoint_whatever_the_otel_environment_says(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector.invalid:4318/v1/traces")
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        provider = telemetry.configure("sherpa-mcp", endpoint)

        (processor,) = _processors(provider)
        assert isinstance(processor, BatchSpanProcessor)
        assert processor.span_exporter._endpoint == "http://127.0.0.1:4318/v1/traces"  # type: ignore[attr-defined]

    def test_service_name_is_the_argument(self) -> None:
        provider = telemetry.configure("sherpa-other", None)

        assert provider.resource.attributes["service.name"] == "sherpa-other"

    def test_a_second_call_keeps_the_first_provider(self, fresh_telemetry: Mock) -> None:
        first = telemetry.configure("sherpa-mcp", None)

        second = telemetry.configure("another", "http://127.0.0.1:4318")

        assert second is first
        assert fresh_telemetry.call_count == 1
        assert _processors(second) == []
