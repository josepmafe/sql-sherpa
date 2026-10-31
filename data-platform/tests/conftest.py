"""Fixtures shared by the tests that span the generator and dbt: the warehouse logins and a dbt runner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import psycopg
import pytest
from dbt.cli.main import dbtRunner
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from dbt.cli.main import dbtRunnerResult

DBT_DIR = Path(__file__).parents[1] / "dbt"

Database = Literal["main", "eval"]
Role = Literal["admin", "agent", "ui"]


class WarehouseSettings(BaseSettings):
    """The admin, agent and UI logins on the main and the eval database, read from `.env`."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    eval_db_name: str = "sherpa_eval"
    admin_user: str
    admin_password: SecretStr
    agent_user: str
    agent_password: SecretStr
    ui_user: str
    ui_password: SecretStr

    def conninfo(self, database: Database, role: Role) -> str:
        credentials = {
            "admin": (self.admin_user, self.admin_password),
            "agent": (self.agent_user, self.agent_password),
            "ui": (self.ui_user, self.ui_password),
        }
        user, password = credentials[role]
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name if database == "main" else self.eval_db_name,
            user=user,
            password=password.get_secret_value(),
        )


@pytest.fixture
def settings() -> WarehouseSettings:
    return WarehouseSettings()  # type: ignore[call-arg]


@pytest.fixture
def database() -> Database:
    """The database a connection fixture opens; a test module overrides it to cover the eval database too."""
    return "main"


@pytest.fixture
def admin(settings: WarehouseSettings, database: Database) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.conninfo(database, "admin"), autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def dbt() -> Callable[..., dbtRunnerResult]:
    """Runs `dbt <args>` in-process in `data-platform/dbt`, with the profiles in the same directory."""

    def run(*args: str) -> dbtRunnerResult:
        result = dbtRunner().invoke([*args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)])
        if not result.success:
            raise AssertionError(f"dbt {' '.join(args)} failed: {result.exception or result.result}")
        return result

    return run
