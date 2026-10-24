from __future__ import annotations

from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# libpq waits ~2 minutes for an unreachable host by default; fail fast instead.
CONNECT_TIMEOUT_SECONDS = 10
# Defence in depth next to the read-only role's grants (agent spec §4): every session is read-only and short.
AGENT_SESSION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5s"


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    agent_user: str
    agent_password: SecretStr
    dbt_target_path: Path = Path("data-platform/dbt/target-prod")
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    # Unprefixed on purpose: MCP_TRANSPORT is the variable the server's launch configuration sets (spec §10).
    transport: Literal["stdio", "http"] = Field(default="stdio", validation_alias="MCP_TRANSPORT")

    def agent_conninfo(self) -> str:
        """libpq connection string for the read-only agent role."""
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.agent_user,
            password=self.agent_password.get_secret_value(),
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=AGENT_SESSION_OPTIONS,
        )
