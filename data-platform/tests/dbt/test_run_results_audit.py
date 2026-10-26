"""The `ops.dbt_run_results` audit table that the on-run-end hook fills (data platform spec §8, "Added by ui-1").

Needs `make build-prod`. The tests that run dbt need `.env` and uv on the PATH. They build nothing (a compile, one
data test, the data tests) and write their artifacts to `target-scratch`, so the warehouse's data and `target-prod`
stay as the build left them; their runs add rows to the audit table, never as full builds.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
SCRATCH = ("--target-path", "target-scratch")
PROD = ("--target", "prod", *SCRATCH)
# What `dbt build` runs and records: models other than ephemeral ones, seeds, snapshots and tests, and unit tests
# (kept apart in the manifest, under `unit_tests`).
BUILT_RESOURCE_TYPES = frozenset({"model", "seed", "snapshot", "test"})
LATEST_INVOCATION = (
    "select invocation_id, is_full_run from ops.dbt_run_results"
    " where target_name = 'prod' and command = %s order by run_started_at desc, invocation_id limit 1"
)
# The other tests run dbt after this one in a full file run, but before it on a rerun, so look past them.
LATEST_FULL_INVOCATION = (
    "select invocation_id from ops.dbt_run_results"
    " where target_name = 'prod' and command = 'build' and is_full_run order by run_started_at desc limit 1"
)
# invocation_args_dict as dbt fills it for each kind of invocation, and whether that run is a full one.
ARGS_CASES: tuple[tuple[str, bool], ...] = (
    ("{}", True),
    ("{'select': (), 'exclude': (), 'resource_types': (), 'exclude_resource_types': (), 'empty': False}", True),
    ("{'full_refresh': True, 'fail_fast': True}", True),
    # --defer and --state only change where unselected nodes resolve, and a run without a selection has none.
    ("{'defer': True, 'state': 'target-prod', 'favor_state': True}", True),
    ("{'select': ('dim_date',)}", False),
    ("{'exclude': ('dim_date',)}", False),
    ("{'selector': 'ci'}", False),
    ("{'resource_types': ('test',)}", False),
    ("{'exclude_resource_types': ('seed',)}", False),
    ("{'empty': True}", False),  # every model built from zero-row inputs
    ("{'sample': '3 days'}", False),  # every model built from a sample of its inputs
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


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    content: dict[str, Any] = json.loads((DBT_DIR / "target-prod" / "manifest.json").read_text(encoding="utf-8"))
    return content


@pytest.fixture(scope="module")
def full_run_verdicts() -> list[str]:
    """`audit_is_full_run` for this compile's own arguments (it runs with --empty), then for each of ARGS_CASES: one
    dbt invocation for all of them."""
    calls = ["invocation_args_dict", *(args for args, _ in ARGS_CASES)]
    inline = "|".join(f"{{{{ audit_is_full_run({args}) }}}}" for args in calls)
    return _dbt("compile", "--empty", "--quiet", *SCRATCH, "--inline", inline).strip().split("|")


def test_full_prod_build_records_every_built_node_once(admin: psycopg.Connection, manifest: dict[str, Any]) -> None:
    latest = admin.execute(LATEST_FULL_INVOCATION).fetchone()
    assert latest is not None
    built = {
        unique_id
        for unique_id, node in manifest["nodes"].items()
        if node["resource_type"] in BUILT_RESOURCE_TYPES and node["config"].get("materialized") != "ephemeral"
    } | set(manifest.get("unit_tests", {}))

    rows = admin.execute(
        "select unique_id, is_full_run from ops.dbt_run_results where invocation_id = %s", (latest[0],)
    ).fetchall()

    assert sorted(unique_id for unique_id, _ in rows) == sorted(built)
    assert all(is_full_run for _, is_full_run in rows)


def test_a_selected_build_is_not_full(admin: psycopg.Connection, manifest: dict[str, Any]) -> None:
    # One data test of a mart: the build reads the warehouse and changes nothing in it.
    test_id, test_name = next(
        (unique_id, node["name"])
        for unique_id, node in sorted(manifest["nodes"].items())
        if node["resource_type"] == "test" and node.get("attached_node") == "model.sherpa.dim_date"
    )

    _dbt("build", "--select", test_name, *PROD)

    latest = admin.execute(LATEST_INVOCATION, ("build",)).fetchone()
    assert latest is not None
    recorded = admin.execute(
        "select unique_id from ops.dbt_run_results where invocation_id = %s", (latest[0],)
    ).fetchall()

    assert latest[1] is False
    assert recorded == [(test_id,)]


def test_a_run_limited_to_a_resource_type_is_not_full(admin: psycopg.Connection) -> None:
    # Every data test and nothing else; read-only, and recorded as command `test`, which the UI never reads.
    _dbt("test", "--resource-type", "test", *PROD)

    latest = admin.execute(LATEST_INVOCATION, ("test",)).fetchone()

    assert latest is not None
    assert latest[1] is False


def test_an_empty_run_is_not_full(full_run_verdicts: list[str]) -> None:
    assert full_run_verdicts[0] == "False"


@pytest.mark.parametrize(("index", "args", "full"), [(index, *case) for index, case in enumerate(ARGS_CASES, 1)])
def test_which_invocations_are_full_runs(full_run_verdicts: list[str], index: int, args: str, full: bool) -> None:
    assert full_run_verdicts[index] == str(full), args


def _dbt(*args: str) -> str:
    completed = subprocess.run(
        ["uv", "run", "--env-file", ENV_FILE, "dbt", *args],
        cwd=DBT_DIR,
        env={**os.environ, "DBT_PROFILES_DIR": "."},
        check=True,
        capture_output=True,
        text=True,
        timeout=DBT_TIMEOUT_SECONDS,
    )
    return completed.stdout
