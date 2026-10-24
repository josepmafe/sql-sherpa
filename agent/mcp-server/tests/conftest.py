from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sherpa_mcp.database import AgentDatabase
from sherpa_mcp.settings import McpSettings

if TYPE_CHECKING:
    from collections.abc import Iterator


class AdminSettings(BaseSettings):
    """The admin login the warehouse tests use for setup checks, read from `.env`."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    admin_user: str
    admin_password: SecretStr

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.admin_user,
            password=self.admin_password.get_secret_value(),
        )


@pytest.fixture(scope="session")
def warehouse_settings() -> McpSettings:
    return McpSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture(scope="session")
def admin_conninfo() -> str:
    return AdminSettings().conninfo()  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def agent_database(warehouse_settings: McpSettings) -> Iterator[AgentDatabase]:
    database = AgentDatabase(warehouse_settings)
    yield database
    database.close()


@pytest.fixture
def admin(admin_conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        yield conn
