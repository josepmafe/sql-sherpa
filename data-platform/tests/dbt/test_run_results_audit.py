"""The `ops.dbt_run_results` audit table that the on-run-end hook fills (data platform spec §8, "Added by ui-1").

Needs `make build-prod`. The selected-build test runs dbt itself, so it needs `.env` and uv on the PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterator

DBT_DIR = Path(__file__).parents[2] / "dbt"
ENV_FILE = "../../.env"  # relative to DBT_DIR: uv mishandles an absolute path with spaces
DBT_TIMEOUT_SECONDS = 600
LATEST_INVOCATION = (
    "select invocation_id, is_full_run from ops.dbt_run_results"
    " where target_name = 'prod' and command = 'build' order by run_started_at desc, invocation_id limit 1"
)
# The selected-build test runs after this one in a full file run but before it on a rerun, so look past it.
LATEST_FULL_INVOCATION = (
    "select invocation_id from ops.dbt_run_results"
    " where target_name = 'prod' and command = 'build' and is_full_run order by run_started_at desc limit 1"
)

pytestmark = pytest.mark.warehouse


class AdminSettings(BaseSettings):
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


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    with psycopg.connect(AdminSettings().conninfo(), autocommit=True) as conn:
        yield conn


def test_full_prod_build_is_recorded(admin: psycopg.Connection) -> None:
    latest = admin.execute(LATEST_FULL_INVOCATION).fetchone()
    assert latest is not None
    invocation_id = latest[0]
    manifest = json.loads((DBT_DIR / "target-prod" / "manifest.json").read_text(encoding="utf-8"))
    models = {
        unique_id
        for unique_id, node in manifest["nodes"].items()
        if node["resource_type"] == "model" and node["config"]["materialized"] != "ephemeral"
    }

    recorded = admin.execute(
        "select unique_id from ops.dbt_run_results where invocation_id = %s and resource_type = 'model'",
        (invocation_id,),
    ).fetchall()

    assert models <= {row[0] for row in recorded}


def test_selected_build_is_not_full(admin: psycopg.Connection) -> None:
    _dbt("build", "--select", "dim_date", "--target", "prod", "--target-path", "target-prod")

    latest = admin.execute(LATEST_INVOCATION).fetchone()

    assert latest is not None
    assert latest[1] is False


def test_run_operation_adds_no_rows(admin: psycopg.Connection) -> None:
    before = _row_count(admin)

    _dbt("run-operation", "grant_read_access", "--target", "prod", "--target-path", "target-prod")

    assert _row_count(admin) == before


def _row_count(admin: psycopg.Connection) -> int:
    row = admin.execute("select count(*) from ops.dbt_run_results").fetchone()
    assert row is not None
    return row[0]


def _dbt(*args: str) -> None:
    subprocess.run(
        ["uv", "run", "--env-file", ENV_FILE, "dbt", *args],
        cwd=DBT_DIR,
        env={**os.environ, "DBT_PROFILES_DIR": "."},
        check=True,
        capture_output=True,
        timeout=DBT_TIMEOUT_SECONDS,
    )
