from __future__ import annotations

from pathlib import Path

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# libpq waits ~2 minutes for an unreachable host by default; fail fast instead.
CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30  # UI spec §5
DEFAULT_ROW_CAP = 1000  # UI spec §5
# Defence in depth next to the role's own defaults (UI spec §5): every session is read-only, with the UI's timeout.
UI_SESSION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout={seconds}s"


class UiSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    ui_user: str
    ui_password: SecretStr
    # At least 1 s: Postgres reads a statement_timeout of 0 as no timeout at all.
    ui_statement_timeout_s: int = Field(default=DEFAULT_STATEMENT_TIMEOUT_SECONDS, ge=1)
    ui_row_cap: int = Field(default=DEFAULT_ROW_CAP, ge=1)
    # SHERPA_UI_DBT_TARGET_PATH (UI spec §9), apart from the MCP server's SHERPA_DBT_TARGET_PATH.
    ui_dbt_target_path: Path = Path("data-platform/dbt/target-prod")

    def ui_conninfo(self) -> str:
        """libpq connection string for the read-only UI role."""
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.ui_user,
            password=self.ui_password.get_secret_value(),
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=UI_SESSION_OPTIONS.format(seconds=self.ui_statement_timeout_s),
        )
