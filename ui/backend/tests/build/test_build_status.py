from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import errors, sql
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sherpa_ui.settings import UiSettings
    from tests.conftest import AdminSettings

# The columns of the table that record_run_results creates (data-platform/dbt/macros/record_run_results.sql).
CREATE_TABLE = """
create schema ops;
create table ops.dbt_run_results (
    invocation_id text not null, target_name text not null, command text not null, is_full_run boolean not null,
    unique_id text not null, resource_type text not null, status text not null, failures integer,
    execution_time_s double precision, message text, run_started_at timestamptz not null
)
"""
INSERT_ROW = "insert into ops.dbt_run_results values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
NEWEST = "2026-10-27T10:00:00+00"
OLDER = "2026-10-26T10:00:00+00"
NEWER_THAN_NEWEST = "2026-10-27T12:00:00+00"


def _insert(
    admin: psycopg.Connection,
    invocation: str,
    started: str,
    *,
    target: str = "prod",
    command: str = "build",
    full: bool = True,
    unique_id: str = "model.p.a",
    status: str = "success",
) -> None:
    admin.execute(
        INSERT_ROW,
        [invocation, target, command, full, unique_id, unique_id.split(".")[0], status, 0, 1.5, "ok", started],
    )


@pytest.fixture
def scratch_settings(ui_settings: UiSettings, admin_settings: AdminSettings) -> Iterator[UiSettings]:
    """Settings for a throwaway database, so no test writes to the real warehouse."""
    name = f"sherpa_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_settings.conninfo(), autocommit=True) as server:
        server.execute(sql.SQL("create database {}").format(sql.Identifier(name)))

    try:
        yield ui_settings.model_copy(update={"db_name": name})
    finally:
        with psycopg.connect(admin_settings.conninfo(), autocommit=True) as server:
            server.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


@pytest.fixture
def scratch_admin(scratch_settings: UiSettings, admin_settings: AdminSettings) -> Iterator[psycopg.Connection]:
    scratch = admin_settings.model_copy(update={"db_name": scratch_settings.db_name})
    with psycopg.connect(scratch.conninfo(), autocommit=True) as conn:
        yield conn


@pytest.fixture
def ops_table(scratch_admin: psycopg.Connection, scratch_settings: UiSettings) -> psycopg.Connection:
    scratch_admin.execute(CREATE_TABLE)
    role = sql.Identifier(scratch_settings.ui_user)
    scratch_admin.execute(sql.SQL("grant usage on schema ops to {}").format(role))
    scratch_admin.execute(sql.SQL("grant select on ops.dbt_run_results to {}").format(role))
    return scratch_admin


@pytest.mark.integration
class TestBuildStatusReaderOnScratchDatabase:
    def test_returns_the_newest_full_prod_build_only(
        self, ops_table: psycopg.Connection, scratch_settings: UiSettings
    ) -> None:
        _insert(ops_table, "old-full", OLDER, unique_id="model.p.old")
        _insert(ops_table, "newest-full", NEWEST, unique_id="model.p.a", status="error")
        _insert(ops_table, "newest-full", NEWEST, unique_id="test.p.t", status="fail")
        _insert(ops_table, "partial", NEWER_THAN_NEWEST, full=False, unique_id="model.p.partial")
        _insert(ops_table, "dev", NEWER_THAN_NEWEST, target="dev", unique_id="model.p.dev")
        _insert(ops_table, "run", NEWER_THAN_NEWEST, command="run", unique_id="model.p.run")

        build = BuildStatusReader(scratch_settings).last_full_build()

        assert build is not None
        assert build.invocation_id == "newest-full"
        assert build.started_at.isoformat() == "2026-10-27T10:00:00+00:00"
        assert build.node_count == 2
        assert set(build.results) == {"model.p.a", "test.p.t"}
        assert build.results["model.p.a"].status == "error"
        assert build.results["test.p.t"].resource_type == "test"
        assert build.results["test.p.t"].failures == 0
        assert build.results["test.p.t"].execution_time_s == 1.5
        assert build.results["test.p.t"].message == "ok"

    def test_none_without_a_full_build(self, ops_table: psycopg.Connection, scratch_settings: UiSettings) -> None:
        _insert(ops_table, "partial", NEWEST, full=False)
        _insert(ops_table, "dev", NEWEST, target="dev")

        assert BuildStatusReader(scratch_settings).last_full_build() is None

    def test_none_when_ops_is_missing(self, scratch_settings: UiSettings) -> None:
        assert BuildStatusReader(scratch_settings).last_full_build() is None

    def test_none_when_the_table_is_missing(
        self, scratch_admin: psycopg.Connection, scratch_settings: UiSettings
    ) -> None:
        scratch_admin.execute("create schema ops")
        scratch_admin.execute(
            sql.SQL("grant usage on schema ops to {}").format(sql.Identifier(scratch_settings.ui_user))
        )

        assert BuildStatusReader(scratch_settings).last_full_build() is None

    def test_unavailable_without_the_grant_on_ops(
        self, scratch_admin: psycopg.Connection, scratch_settings: UiSettings
    ) -> None:
        scratch_admin.execute(CREATE_TABLE)

        with pytest.raises(BuildStatusUnavailable, match="missing grant or timeout"):
            BuildStatusReader(scratch_settings).last_full_build()


class TestBuildStatusReaderOffline:
    def test_unavailable_when_the_database_is_down(self, offline_settings: UiSettings) -> None:
        with pytest.raises(BuildStatusUnavailable):
            BuildStatusReader(offline_settings).last_full_build()

    def test_unavailable_when_the_statement_timeout_cancels_the_read(
        self, settings: UiSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def cancelled(*_: object, **__: object) -> None:
            raise errors.QueryCanceled("canceling statement due to statement timeout")

        monkeypatch.setattr(psycopg, "connect", cancelled)

        with pytest.raises(BuildStatusUnavailable):
            BuildStatusReader(settings).last_full_build()
