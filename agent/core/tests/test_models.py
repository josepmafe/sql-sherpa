from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from sherpa_agent import models
from sherpa_agent.models import AgentError, build_model, model_settings
from sherpa_agent.settings import AgentSettings

if TYPE_CHECKING:
    from botocore.client import BaseClient

FORCED_SETTINGS = {"temperature", "top_p", "tool_choice"}
REQUESTED_PROFILES: list[str | None] = []
REAL_SESSION = boto3.Session


class StubSession:
    """Stands in for `boto3.Session`: fake credentials, no profile file and no network."""

    def __init__(self, profile_name: str | None = None) -> None:
        REQUESTED_PROFILES.append(profile_name)
        self._real = REAL_SESSION(aws_access_key_id="test", aws_secret_access_key="test")

    def client(self, *args: Any, **kwargs: Any) -> BaseClient:
        return self._real.client(*args, **kwargs)  # type: ignore[no-any-return]


class TestBuildModel:
    def test_bedrock_prefix_builds_a_converse_model_with_the_retry_and_timeout_config(
        self, settings: AgentSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(models.boto3, "Session", StubSession)

        model = build_model("bedrock:eu.anthropic.claude-sonnet-5-5", settings)

        assert isinstance(model, BedrockConverseModel)
        assert model.model_name == "eu.anthropic.claude-sonnet-5-5"
        config = model.client.meta.config
        assert config.retries["mode"] == "adaptive"
        assert config.retries["total_max_attempts"] == 6  # botocore reads max_attempts=5 as five retries
        assert config.connect_timeout == 5
        assert config.read_timeout == 300
        assert model.client.meta.region_name == "eu-central-1"
        assert REQUESTED_PROFILES[-1] == "ClaudeCode"

    def test_anthropic_prefix_builds_the_anthropic_model(
        self, settings: AgentSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        model = build_model("anthropic:claude-sonnet-4-5", settings)

        assert isinstance(model, AnthropicModel)
        assert model.model_name == "claude-sonnet-4-5"

    @pytest.mark.parametrize("model_string", ["openai:gpt-x", "claude-sonnet", ""])
    def test_any_other_prefix_names_the_two_accepted_ones(self, model_string: str, settings: AgentSettings) -> None:
        with pytest.raises(AgentError, match=r"bedrock:.*anthropic:"):
            build_model(model_string, settings)


class TestModelSettings:
    def test_bedrock_puts_the_effort_in_the_additional_request_fields(self) -> None:
        result = model_settings("bedrock:eu.anthropic.claude-haiku-5-5", "high")

        assert result["bedrock_additional_model_requests_fields"] == {"output_config": {"effort": "high"}}  # type: ignore[typeddict-item]
        assert result["max_tokens"] == 16_000

    def test_anthropic_puts_the_effort_in_its_own_setting(self) -> None:
        result = model_settings("anthropic:claude-sonnet-4-5", "low")

        assert result["anthropic_effort"] == "low"  # type: ignore[typeddict-item]
        assert result["max_tokens"] == 16_000

    @pytest.mark.parametrize("model_string", ["bedrock:x", "anthropic:x"])
    def test_no_setting_forces_sampling_or_tool_choice(self, model_string: str) -> None:
        result = model_settings(model_string, "medium")

        assert not FORCED_SETTINGS & set(result)

    def test_an_unknown_prefix_is_an_agent_error(self) -> None:
        with pytest.raises(AgentError, match="bedrock:"):
            model_settings("openai:x", "medium")


class TestAgentSettings:
    def test_defaults_come_from_the_spec_section_10(self, settings: AgentSettings) -> None:
        assert settings.model_default == "bedrock:eu.anthropic.claude-sonnet-5-5"
        assert settings.model_fast == "bedrock:eu.anthropic.claude-haiku-5-5"
        assert settings.effort_default == "medium"
        assert settings.effort_fast == "medium"
        assert settings.default_variant == "v0_baseline"
        assert (settings.aws_profile, settings.aws_region) == ("ClaudeCode", "eu-central-1")
        assert settings.mcp_transport == "stdio"
        assert settings.mcp_http_url == "http://127.0.0.1:8765/mcp"
        assert settings.otel_exporter_otlp_endpoint is None
        assert settings.trace_url_template is None

    def test_the_unprefixed_spec_variables_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_PROFILE", "p")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        monkeypatch.setenv("MCP_HTTP_URL", "http://h:1/mcp")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://x:4318")

        s = AgentSettings(_env_file=None)  # type: ignore[call-arg]

        assert (s.aws_profile, s.aws_region, s.mcp_transport) == ("p", "eu-west-1", "http")
        assert (s.mcp_http_url, s.otel_exporter_otlp_endpoint) == ("http://h:1/mcp", "http://x:4318")

    def test_an_unknown_effort_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_EFFORT_DEFAULT", "turbo")

        with pytest.raises(ValueError, match="effort_default"):
            AgentSettings(_env_file=None)  # type: ignore[call-arg]
