"""The `ops.dbt_run_results` audit table that the on-run-end hook fills (data platform spec §8, "Added by ui-1").

Needs `make build-prod` and `.env`. The tests that run dbt (in-process, via the `dbt` fixture) build nothing (a
compile and one data test) and write their artifacts to `target-scratch`, so the warehouse's data
and `target-prod` stay as the build left them; their runs add rows to the audit table, never as full builds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    import psycopg
    from dbt.cli.main import dbtRunnerResult

DBT_DIR = Path(__file__).parents[2] / "dbt"
SCRATCH = ("--target-path", "target-scratch")
PROD = ("--target", "prod", *SCRATCH)
# What `dbt build` runs and records: models other than ephemeral ones, seeds, snapshots and tests, and unit tests
# (kept apart in the manifest, under `unit_tests`).
BUILT_RESOURCE_TYPES = frozenset({"model", "seed", "snapshot", "test"})
# The other tests run dbt after this one in a full file run, but before it on a rerun, so look past them, and past
# any build that started after this checkout's manifest was generated.
LATEST_FULL_INVOCATION = (
    "select invocation_id from ops.dbt_run_results"
    " where target_name = 'prod' and command = 'build' and is_full_run and run_started_at <= %s"
    " order by run_started_at desc limit 1"
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


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    content: dict[str, Any] = json.loads((DBT_DIR / "target-prod" / "manifest.json").read_text(encoding="utf-8"))
    return content


@pytest.fixture(scope="module")
def full_run_verdicts(dbt: Callable[..., dbtRunnerResult]) -> list[str]:
    """`audit_is_full_run` for this compile's own arguments (it runs with --empty), then for each of ARGS_CASES: one
    dbt invocation for all of them."""
    calls = ["invocation_args_dict", *(args for args, _ in ARGS_CASES)]
    inline = "|".join(f"{{{{ audit_is_full_run({args}) }}}}" for args in calls)

    result = dbt("compile", "--empty", "--quiet", *SCRATCH, "--inline", inline)

    return result.result.results[0].node.compiled_code.strip().split("|")


@pytest.fixture(scope="module")
def selected_build(manifest: dict[str, Any], dbt: Callable[..., dbtRunnerResult]) -> tuple[str, str]:
    """The data test of a mart that one `dbt build --select` ran: its unique id and the invocation id of the run."""
    # One data test of a mart: the build reads the warehouse and changes nothing in it.
    test_id, test_name = _data_test_of(manifest, "model.sherpa.dim_date")

    dbt("build", "--select", test_name, *PROD)

    return test_id, _own_invocation_id()


def test_full_prod_build_records_every_built_node_once(admin: psycopg.Connection, manifest: dict[str, Any]) -> None:
    """Compares the latest full prod build that started before this checkout's manifest was generated. Another
    checkout that builds prod between this one's build and its manifest still goes unnoticed."""
    latest = admin.execute(LATEST_FULL_INVOCATION, (manifest["metadata"]["generated_at"],)).fetchone()  # type: ignore[call-overload]
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


def test_reads_its_own_invocation(admin: psycopg.Connection, selected_build: tuple[str, str]) -> None:
    # Another checkout can build prod at the same time, so the rows are found by this run's own invocation id.
    test_id, invocation_id = selected_build

    recorded = admin.execute(
        "select unique_id from ops.dbt_run_results where invocation_id = %s", (invocation_id,)
    ).fetchall()

    assert recorded == [(test_id,)]


def test_a_selected_build_is_not_full(admin: psycopg.Connection, selected_build: tuple[str, str]) -> None:
    _, invocation_id = selected_build

    assert _is_full_run(admin, invocation_id) is False


def test_an_empty_run_is_not_full(full_run_verdicts: list[str]) -> None:
    assert full_run_verdicts[0] == "False"


@pytest.mark.parametrize(("index", "args", "full"), [(index, *case) for index, case in enumerate(ARGS_CASES, 1)])
def test_which_invocations_are_full_runs(full_run_verdicts: list[str], index: int, args: str, full: bool) -> None:
    assert full_run_verdicts[index] == str(full), args


def _data_test_of(manifest: dict[str, Any], model_id: str) -> tuple[str, str]:
    return next(
        (unique_id, node["name"])
        for unique_id, node in sorted(manifest["nodes"].items())
        if node["resource_type"] == "test" and node.get("attached_node") == model_id
    )


def _own_invocation_id() -> str:
    # The run that just finished wrote this file into its own target path.
    run_results = json.loads((DBT_DIR / "target-scratch" / "run_results.json").read_text(encoding="utf-8"))
    return str(run_results["metadata"]["invocation_id"])


def _is_full_run(admin: psycopg.Connection, invocation_id: str) -> bool:
    row = admin.execute(
        "select bool_and(is_full_run) from ops.dbt_run_results where invocation_id = %s", (invocation_id,)
    ).fetchone()
    assert row is not None
    assert row[0] is not None, f"invocation {invocation_id} recorded nothing"
    return bool(row[0])
