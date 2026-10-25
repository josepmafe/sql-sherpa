from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_ui.readonly.query_runner import QueryRunner
from sherpa_ui.settings import UiSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCALHOST = "127.0.0.1"
BACKEND_EXIT_SECONDS = 2.0  # how long the server process of a closed connection may take to leave pg_stat_activity
UI_BACKENDS = "select count(*) from pg_stat_activity where usename = %s and backend_type = 'client backend'"


class AdminSettings(BaseSettings):
    """The admin login the warehouse tests use for their checks, read from `.env`."""

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
def ui_settings() -> UiSettings:
    return UiSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture(scope="module")
def prod_loader(ui_settings: UiSettings) -> MartCatalogLoader:
    return MartCatalogLoader(REPO_ROOT / ui_settings.ui_dbt_target_path)


@pytest.fixture(scope="module")
def runner(ui_settings: UiSettings, prod_loader: MartCatalogLoader) -> QueryRunner:
    return QueryRunner(ui_settings, prod_loader)


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    with psycopg.connect(AdminSettings().conninfo(), autocommit=True) as conn:  # type: ignore[call-arg]
        yield conn


@pytest.fixture
def leaked_ui_backends(admin: psycopg.Connection, ui_settings: UiSettings) -> Callable[[], int]:
    """How many more server processes the UI role has open than when the test started, once closed connections
    have had `BACKEND_EXIT_SECONDS` to go away."""

    def count() -> int:
        row = admin.execute(UI_BACKENDS, [ui_settings.ui_user]).fetchone()
        assert row is not None
        return int(row[0])

    baseline = count()

    def leaked() -> int:
        deadline = time.monotonic() + BACKEND_EXIT_SECONDS
        current = count()
        while current > baseline and time.monotonic() < deadline:
            time.sleep(0.05)
            current = count()
        return max(current - baseline, 0)

    return leaked


@pytest.fixture
def closed_port() -> int:
    """A loopback port nothing listens on: free a moment ago, and nothing here binds it."""
    with socket.socket() as sock:
        sock.bind((LOCALHOST, 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def settings(tmp_path: Path) -> UiSettings:
    return UiSettings(
        _env_file=None,  # type: ignore[call-arg]
        db_host="127.0.0.1",
        db_port=1,
        db_name="sherpa",
        ui_user="sherpa_ui",
        ui_password=SecretStr("x"),
        ui_dbt_target_path=tmp_path / "missing-target",
    )
