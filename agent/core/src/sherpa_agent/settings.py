from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentSettings(BaseSettings):
    """Agent spec §10. The unprefixed variables are the ones the spec names that way (and the AWS SDK reads itself)."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    model_default: str = "bedrock:eu.anthropic.claude-sonnet-5-5"
    model_fast: str = "bedrock:eu.anthropic.claude-haiku-5-5"
    effort_default: Effort = "medium"
    effort_fast: Effort = "medium"
    default_variant: str = "v0_baseline"
    trace_url_template: str | None = None

    aws_profile: str = Field(default="ClaudeCode", validation_alias="AWS_PROFILE")
    aws_region: str = Field(default="eu-central-1", validation_alias="AWS_REGION")
    mcp_transport: Literal["stdio", "http"] = Field(default="stdio", validation_alias="MCP_TRANSPORT")
    mcp_http_url: str = Field(default="http://127.0.0.1:8765/mcp", validation_alias="MCP_HTTP_URL")
    otel_exporter_otlp_endpoint: str | None = Field(default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")

    # What the stdio client passes on to the MCP server it launches.
    db_host: str
    db_port: int
    db_name: str
    agent_user: str
    agent_password: SecretStr
    dbt_target_path: Path = Path("data-platform/dbt/target-prod")
