from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import psycopg
import pytest
from psycopg.postgres import types as postgres_types
from pydantic import SecretStr
from sherpa_mcp.catalog import MartCatalog, MartCatalogLoader
from sherpa_ui.readonly.query_runner import QueryRunner, UiQueryError, user_position
from sherpa_ui.readonly.sort_filter import DuplicateColumnsError, FilterSpec, SortSpec
from sherpa_ui.settings import UiSettings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from unittest.mock import MagicMock

# An aggregate, so no row comes back before the whole cross join is counted (UI spec §8): far longer than any test.
LONG_AGGREGATE = (
    "select count(*) as n from marts.fct_order_items a "
    "cross join marts.fct_order_items b cross join marts.fct_order_items c"
)
REGISTER_WAIT_SECONDS = 10.0
DECLARE_OFFSET = len('DECLARE "sherpa_ui" CURSOR FOR ')
INT4_OID = postgres_types["int4"].oid


@pytest.fixture
def offline_settings(closed_port: int) -> UiSettings:
    return UiSettings(
        _env_file=None,  # type: ignore[call-arg]
        db_host="127.0.0.1",
        db_port=closed_port,
        db_name="sherpa",
        ui_user="sherpa_ui",
        ui_password=SecretStr("x"),
    )


@pytest.fixture
def empty_catalog_loader() -> MartCatalogLoader:
    """A loader whose catalog has no marts: enough for SQL that reads no table."""
    loader = create_autospec(MartCatalogLoader, instance=True)
    loader.load.return_value = MartCatalog(marts=(), metrics=(), generated_at=datetime(2026, 10, 1, tzinfo=UTC))
    return loader


@pytest.fixture
def offline_runner(offline_settings: UiSettings, empty_catalog_loader: MartCatalogLoader) -> QueryRunner:
    return QueryRunner(offline_settings, empty_catalog_loader)


@pytest.mark.parametrize(
    ("statement_position", "expected"),
    [
        (None, None),
        ("1", None),  # in the cursor declaration
        (str(DECLARE_OFFSET), None),  # its last character
        (str(DECLARE_OFFSET + 1), 0),  # the user's first character
        (str(DECLARE_OFFSET + 10), 9),  # the user's last character
        (str(DECLARE_OFFSET + 11), 10),  # "at end of input": just past it
        (str(DECLARE_OFFSET + 12), None),  # in text after the user's SQL
    ],
)
def test_user_position_maps_postgres_positions_into_the_users_sql(
    statement_position: str | None, expected: int | None
) -> None:
    assert user_position(statement_position, offset=DECLARE_OFFSET, length=10) == expected


class TestQueryRunnerOffline:
    """What the runner decides before or without a working warehouse connection."""

    def test_missing_artifacts_are_catalog_unavailable(self, offline_settings: UiSettings, tmp_path: Path) -> None:
        runner = QueryRunner(offline_settings, MartCatalogLoader(tmp_path))

        with pytest.raises(UiQueryError) as error:
            runner.run("q1", "select 1 as a")

        assert error.value.code == "catalog_unavailable"
        assert error.value.message.startswith("dbt artifacts not found (manifest.json, catalog.json)")
        assert error.value.position is None

    def test_guard_rejection_never_connects(self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(psycopg, "connect", _must_not_connect)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "")

        assert error.value.code == "guard_rejected"
        assert error.value.message == "[guard:statement_count] expected exactly one statement, got 0; send one SELECT"
        assert offline_runner.running_query_ids() == frozenset()

    def test_unreachable_database_is_database_unavailable(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A refused connection takes the whole connect timeout on Windows, so the failure is raised directly.
        def refused(*args: object, **kwargs: object) -> None:
            raise psycopg.OperationalError("connection failed: connection refused\n\tIs the server running?")

        monkeypatch.setattr(psycopg, "connect", refused)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a")

        assert error.value.code == "database_unavailable"
        assert error.value.message == (
            "the warehouse is unreachable (connection failed: connection refused); is `make up` running?"
        )
        assert offline_runner.running_query_ids() == frozenset()

    def test_cancel_of_an_unknown_id_is_a_no_op(self, offline_runner: QueryRunner) -> None:
        offline_runner.cancel("never-ran")

        assert offline_runner.running_query_ids() == frozenset()

    def test_cancel_while_connecting_cancels_the_run(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = _fake_connection()

        def connect(*args: object, **kwargs: object) -> MagicMock:
            offline_runner.cancel("q1")  # arrives before the run has a connection to cancel
            return connection

        monkeypatch.setattr(psycopg, "connect", connect)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a")

        assert error.value.code == "cancelled"
        connection.transaction.assert_not_called()
        connection.close.assert_called_once_with()
        assert offline_runner.running_query_ids() == frozenset()

    def test_cancel_while_the_catalog_loads_never_connects(
        self, offline_settings: UiSettings, empty_catalog_loader: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = QueryRunner(offline_settings, empty_catalog_loader)
        catalog = empty_catalog_loader.load.return_value

        def load() -> MartCatalog:
            runner.cancel("q1")  # sent right after the request, before the run has done anything
            return catalog

        empty_catalog_loader.load.side_effect = load
        monkeypatch.setattr(psycopg, "connect", _must_not_connect)

        with pytest.raises(UiQueryError) as error:
            runner.run("q1", "select 1 as a")

        assert error.value.code == "cancelled"
        assert runner.running_query_ids() == frozenset()

    @pytest.mark.parametrize(
        ("columns", "sort", "connect_fails", "cause"),
        [
            (("a",), (), True, "database_unavailable"),
            (("a", "a"), (SortSpec(column="a", direction="asc"),), False, "DuplicateColumnsError"),
            (("a",), (SortSpec(column="b", direction="asc"),), False, "ValueError"),
        ],
    )
    def test_a_cancelled_run_is_cancelled_whatever_else_failed(
        self,
        offline_runner: QueryRunner,
        monkeypatch: pytest.MonkeyPatch,
        columns: tuple[str, ...],
        sort: tuple[SortSpec, ...],
        connect_fails: bool,
        cause: str,
    ) -> None:
        connection = _fake_connection(columns=columns)
        connection.execute.side_effect = lambda *args, **kwargs: offline_runner.cancel("q1")

        def connect(*args: object, **kwargs: object) -> MagicMock:
            if connect_fails:
                offline_runner.cancel("q1")
                raise psycopg.OperationalError("connection failed: connection refused")

            return connection

        monkeypatch.setattr(psycopg, "connect", connect)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a", sort=sort)

        assert error.value.code == "cancelled"
        assert _describe(error.value.__cause__) == cause
        assert offline_runner.running_query_ids() == frozenset()

    def test_duplicate_query_id_keeps_the_running_query(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connected = threading.Event()
        connection = _fake_connection()

        def slow_connect(*args: object, **kwargs: object) -> MagicMock:
            assert connected.wait(REGISTER_WAIT_SECONDS)
            return connection

        monkeypatch.setattr(psycopg, "connect", slow_connect)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(offline_runner.run, "q1", "select 1 as a")
            _wait_until_running(offline_runner, "q1")

            with pytest.raises(UiQueryError) as duplicate:
                offline_runner.run("q1", "select 2 as a")

            still_running = offline_runner.running_query_ids()
            offline_runner.cancel("q1")  # proves the first run's entry survived the duplicate
            connected.set()

            with pytest.raises(UiQueryError) as cancelled:
                first.result(timeout=REGISTER_WAIT_SECONDS)

        assert duplicate.value.code == "duplicate_query_id"
        assert duplicate.value.message == "a query with id q1 is already running"
        assert still_running == frozenset({"q1"})
        assert cancelled.value.code == "cancelled"
        connection.close.assert_called_once_with()
        assert offline_runner.running_query_ids() == frozenset()

    def test_cancel_between_statements_still_ends_the_run_cancelled(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Postgres ignores a cancel request that finds nothing running, so every statement of the run succeeds.
        connection = _fake_connection(rows=[(1,)])
        connection.execute.side_effect = lambda *args, **kwargs: offline_runner.cancel("q1")
        monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a")

        connection.execute.side_effect = None

        again = offline_runner.run("q1", "select 1 as a")  # the cancel ended with the run it reached

        assert error.value.code == "cancelled"
        connection.cancel_safe.assert_called_once_with(timeout=5.0)
        assert again.rows == ((1,),)
        assert connection.close.call_count == 2
        assert offline_runner.running_query_ids() == frozenset()

    def test_a_failed_run_releases_its_id_and_closes_its_connection(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = _fake_connection()
        connection.transaction.side_effect = psycopg.OperationalError("server closed the connection unexpectedly")
        monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a")

        assert error.value.code == "database_unavailable"
        assert error.value.message == (
            "the warehouse is unreachable (server closed the connection unexpectedly); is `make up` running?"
        )
        connection.close.assert_called_once_with()
        assert offline_runner.running_query_ids() == frozenset()

    def test_a_refused_statement_is_an_sql_error(
        self, offline_runner: QueryRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = _fake_connection()
        connection.execute.side_effect = psycopg.errors.InsufficientPrivilege("permission denied for schema ops")
        monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)

        with pytest.raises(UiQueryError) as error:
            offline_runner.run("q1", "select 1 as a")

        assert error.value.code == "sql_error"
        assert error.value.message == "permission denied for schema ops"
        assert error.value.position is None
        connection.close.assert_called_once_with()

    def test_format_pretty_prints_postgres(self, offline_runner: QueryRunner) -> None:
        formatted = offline_runner.format("select order_id, count(*) from marts.fct_orders -- all\ngroup by 1")

        assert formatted == "SELECT\n  order_id,\n  COUNT(*)\nFROM marts.fct_orders /* all */\nGROUP BY\n  1"

    def test_format_keeps_statements_apart(self, offline_runner: QueryRunner) -> None:
        assert offline_runner.format("select 1;; select 2;") == "SELECT\n  1;\n\nSELECT\n  2"

    @pytest.mark.parametrize("sql_text", ["", "  \n", "-- only a note\n", "/* a note */"])
    def test_format_returns_text_without_a_statement_unchanged(
        self, offline_runner: QueryRunner, sql_text: str
    ) -> None:
        assert offline_runner.format(sql_text) == sql_text

    def test_format_of_unparseable_sql_is_an_error(self, offline_runner: QueryRunner) -> None:
        with pytest.raises(UiQueryError) as error:
            offline_runner.format("select 'unterminated")

        assert error.value.code == "sql_error"
        assert error.value.message.startswith("could not format the query: ")
        assert error.value.position is None


@pytest.mark.warehouse
class TestQueryRunner:
    def test_runs_and_caps_at_1000(self, runner: QueryRunner, admin: psycopg.Connection) -> None:
        description = admin.execute("select * from marts.fct_order_items limit 0").description
        assert description is not None

        outcome = runner.run("q1", "select * from marts.fct_order_items")

        assert outcome.query_id == "q1"
        assert outcome.mode == "client"
        assert outcome.columns == tuple(column.name for column in description)
        assert len(outcome.column_types) == len(description)
        assert all(len(row) == len(description) for row in outcome.rows)
        assert len(outcome.rows) == 1000
        assert outcome.returned_rows == 1000
        assert outcome.truncated

    def test_exactly_the_cap_is_not_truncated(self, runner: QueryRunner) -> None:
        at_cap = runner.run("q1", "select order_item_id from marts.fct_order_items limit 1000")
        over_cap = runner.run("q2", "select order_item_id from marts.fct_order_items limit 1001")

        assert (at_cap.returned_rows, at_cap.truncated) == (1000, False)
        assert (over_cap.returned_rows, over_cap.truncated) == (1000, True)

    def test_row_cap_comes_from_the_settings(self, ui_settings: UiSettings, prod_loader: MartCatalogLoader) -> None:
        runner = QueryRunner(ui_settings.model_copy(update={"ui_row_cap": 5}), prod_loader)

        outcome = runner.run("q1", "select order_item_id from marts.fct_order_items")

        assert (outcome.returned_rows, outcome.truncated) == (5, True)

    def test_guard_rejection_has_code_and_reason(self, runner: QueryRunner) -> None:
        with pytest.raises(UiQueryError) as error:
            runner.run("q1", "delete from marts.fct_orders")

        assert error.value.code == "guard_rejected"
        assert error.value.message == (
            "[guard:not_select] the statement must be a SELECT query, not DELETE; write one SELECT"
        )
        assert error.value.position is None

    @pytest.mark.parametrize(
        "sql_text",
        [
            "select nope from marts.fct_orders",
            "\n\n  select nope from marts.fct_orders;",
            "select 'é😀' as e, nope from marts.fct_orders",
        ],
    )
    @pytest.mark.parametrize("sort", [(), (SortSpec(column="order_id", direction="asc"),)], ids=["client", "server"])
    def test_sql_error_position_points_into_user_sql(
        self, runner: QueryRunner, sql_text: str, sort: tuple[SortSpec, ...]
    ) -> None:
        with pytest.raises(UiQueryError) as error:
            runner.run("q1", sql_text, sort=sort)

        assert error.value.code == "sql_error"
        assert error.value.message == 'column "nope" does not exist'
        assert error.value.position == sql_text.index("nope")

    def test_error_in_the_wrapper_has_no_position(self, runner: QueryRunner) -> None:
        with pytest.raises(UiQueryError) as error:
            runner.run(
                "q1", "select '{}'::json as j from marts.fct_orders", sort=[SortSpec(column="j", direction="asc")]
            )

        assert error.value.code == "sql_error"
        assert error.value.message == (
            "could not identify an ordering operator for type json; "
            "Use an explicit ordering operator or modify the query."
        )
        assert error.value.position is None

    def test_postgres_hint_is_appended(self, runner: QueryRunner) -> None:
        with pytest.raises(UiQueryError) as error:
            runner.run("q1", "select emial from marts.dim_customers")

        assert error.value.message == (
            'column "emial" does not exist; Perhaps you meant to reference the column "dim_customers.email".'
        )
        assert error.value.position == len("select ")

    def test_server_mode_sorts_all_rows(self, runner: QueryRunner, admin: psycopg.Connection) -> None:
        maximum = admin.execute("select max(order_item_id) from marts.fct_order_items").fetchone()

        outcome = runner.run(
            "q1", "select * from marts.fct_order_items", sort=[SortSpec(column="order_item_id", direction="desc")]
        )

        assert outcome.mode == "server"
        assert outcome.truncated
        assert maximum is not None
        assert outcome.rows[0][outcome.columns.index("order_item_id")] == maximum[0]

    @pytest.mark.parametrize(
        ("spec", "where"),
        [
            (FilterSpec(column="order_number", operator="contains", value="0001"), "order_number ilike '%0001%'"),
            (FilterSpec(column="cancelled_at", operator="is_not_null"), "cancelled_at is not null"),
            (FilterSpec(column="cancelled_at", operator="is_null"), "cancelled_at is null"),
            (FilterSpec(column="is_cancelled", operator="=", value="true"), "is_cancelled"),
            (FilterSpec(column="order_total", operator=">", value="250"), "order_total > 250"),
            (FilterSpec(column="item_count", operator="<=", value="1"), "item_count <= 1"),
            (FilterSpec(column="current_status", operator="!=", value="delivered"), "current_status <> 'delivered'"),
        ],
    )
    def test_server_mode_filter(
        self, runner: QueryRunner, admin: psycopg.Connection, spec: FilterSpec, where: str
    ) -> None:
        expected = admin.execute(f"select count(*) from marts.fct_orders where {where}").fetchone()
        assert expected is not None

        outcome = runner.run("q1", "select * from marts.fct_orders", filters=[spec])

        assert outcome.mode == "server"
        assert outcome.returned_rows == min(expected[0], 1000)
        assert outcome.truncated == (expected[0] > 1000)

    def test_server_mode_filter_on_a_date(self, runner: QueryRunner, admin: psycopg.Connection) -> None:
        row = admin.execute(
            "select order_date, count(*) from marts.fct_orders group by order_date order by order_date desc limit 1"
        ).fetchone()
        assert row is not None
        last_day, orders_that_day = row

        outcome = runner.run(
            "q1",
            "select order_id, order_date from marts.fct_orders",
            filters=[FilterSpec(column="order_date", operator="=", value=last_day.isoformat())],
        )

        assert outcome.returned_rows == orders_that_day
        assert {row[1] for row in outcome.rows} == {last_day.isoformat()}

    def test_contains_matches_wildcards_literally(self, runner: QueryRunner) -> None:
        # An unescaped `_` would match any character of every order number.
        outcome = runner.run(
            "q1",
            "select order_number from marts.fct_orders",
            filters=[FilterSpec(column="order_number", operator="contains", value="_")],
        )

        assert outcome.returned_rows == 0

    @pytest.mark.parametrize(
        "sql_text",
        [
            "select order_item_id from marts.fct_order_items -- every item",
            "select order_item_id from marts.fct_order_items; -- every item",
            "select order_item_id from marts.fct_order_items -- every item\n;\n",
            "select order_item_id from marts.fct_order_items /* every item */ ;",
        ],
    )
    def test_server_mode_with_trailing_comment(
        self, runner: QueryRunner, admin: psycopg.Connection, sql_text: str
    ) -> None:
        maximum = admin.execute("select max(order_item_id) from marts.fct_order_items").fetchone()

        outcome = runner.run("q1", sql_text, sort=[SortSpec(column="order_item_id", direction="desc")])

        assert maximum is not None
        assert outcome.rows[0] == (maximum[0],)

    def test_client_mode_with_trailing_comment(self, runner: QueryRunner) -> None:
        outcome = runner.run("q1", "select 1 as a; -- one")

        assert outcome.rows == ((1,),)

    def test_server_mode_keeps_percent_signs(self, runner: QueryRunner) -> None:
        sql_text = "select order_item_id % 7 as m, 'a%sb' as t from marts.fct_order_items where 'x%' like '%'"

        client = runner.run("q0", sql_text)
        sorted_only = runner.run("q1", sql_text, sort=[SortSpec(column="m", direction="desc")])
        filtered = runner.run("q2", sql_text, filters=[FilterSpec(column="m", operator="=", value="6")])

        assert client.rows[0][1] == "a%sb"
        assert sorted_only.rows[0] == (6, "a%sb")
        assert filtered.returned_rows > 0
        assert set(filtered.rows) == {(6, "a%sb")}

    def test_duplicate_columns_fall_back_to_client(self, runner: QueryRunner) -> None:
        sql_text = "select order_id, order_id from marts.fct_orders"

        with pytest.raises(DuplicateColumnsError, match="duplicate column names: order_id"):
            runner.run("q1", sql_text, sort=[SortSpec(column="order_id", direction="desc")])

        client = runner.run("q2", sql_text)

        assert client.columns == ("order_id", "order_id")
        assert runner.running_query_ids() == frozenset()

    @pytest.mark.parametrize("sort", [(), (SortSpec(column="n", direction="desc"),)], ids=["client", "server"])
    def test_cancel_stops_a_long_query(
        self, runner: QueryRunner, sort: tuple[SortSpec, ...], leaked_ui_backends: Callable[[], int]
    ) -> None:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(runner.run, "long", LONG_AGGREGATE, sort)
            _wait_until_running(runner, "long")
            time.sleep(1)
            cancelled_at = time.monotonic()
            runner.cancel("long")

            with pytest.raises(UiQueryError) as error:
                running.result(timeout=3)

            took = time.monotonic() - cancelled_at

        again = runner.run("long", "select 1 as n")

        assert error.value.code == "cancelled"
        assert error.value.message == "the query was cancelled"
        assert took < 3
        assert again.rows == ((1,),)
        assert runner.running_query_ids() == frozenset()
        assert leaked_ui_backends() == 0

    def test_cancelling_one_query_leaves_another_running(
        self, runner: QueryRunner, leaked_ui_backends: Callable[[], int]
    ) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(runner.run, "first", LONG_AGGREGATE)
            second = pool.submit(runner.run, "second", LONG_AGGREGATE)
            _wait_until_running(runner, "first")
            _wait_until_running(runner, "second")
            time.sleep(1)

            runner.cancel("first")
            with pytest.raises(UiQueryError) as first_error:
                first.result(timeout=3)

            time.sleep(1)  # time for a stray cancel to reach the second query
            second_still_running = not second.done() and runner.running_query_ids() == frozenset({"second"})
            runner.cancel("second")
            with pytest.raises(UiQueryError) as second_error:
                second.result(timeout=3)

        assert first_error.value.code == "cancelled"
        assert second_still_running
        assert second_error.value.code == "cancelled"
        assert runner.running_query_ids() == frozenset()
        assert leaked_ui_backends() == 0

    def test_late_cancel_is_a_no_op_and_the_id_is_reusable(self, runner: QueryRunner) -> None:
        runner.run("q1", "select 1 as a")

        runner.cancel("q1")
        again = runner.run("q1", "select 2 as a")

        assert again.rows == ((2,),)

    @pytest.mark.parametrize("sort", [(), (SortSpec(column="n", direction="desc"),)], ids=["client", "server"])
    def test_timeout(
        self,
        ui_settings: UiSettings,
        prod_loader: MartCatalogLoader,
        sort: tuple[SortSpec, ...],
        leaked_ui_backends: Callable[[], int],
    ) -> None:
        runner = QueryRunner(ui_settings.model_copy(update={"ui_statement_timeout_s": 1}), prod_loader)
        started = time.monotonic()

        with pytest.raises(UiQueryError) as error:
            runner.run("q1", LONG_AGGREGATE, sort=sort)

        assert error.value.code == "timeout"
        assert error.value.message == "the query ran longer than 1 s and was stopped; filter or aggregate more narrowly"
        assert time.monotonic() - started < 5
        assert runner.running_query_ids() == frozenset()
        assert leaked_ui_backends() == 0

    @pytest.mark.parametrize(
        ("sql_text", "sort", "raises"),
        [
            ("select 1 as a", (), None),
            ("select nope from marts.fct_orders", (), UiQueryError),
            ("select 1 as a, 2 as a", (SortSpec(column="a", direction="asc"),), DuplicateColumnsError),
            ("select 1 as a", (SortSpec(column="b", direction="asc"),), ValueError),
        ],
        ids=["ok", "sql_error", "duplicate_columns", "unknown_column"],
    )
    def test_every_outcome_releases_its_id_and_connection(
        self,
        runner: QueryRunner,
        leaked_ui_backends: Callable[[], int],
        sql_text: str,
        sort: tuple[SortSpec, ...],
        raises: type[Exception] | None,
    ) -> None:
        if raises is None:
            runner.run("q1", sql_text, sort=sort)
        else:
            with pytest.raises(raises):
                runner.run("q1", sql_text, sort=sort)

        assert runner.running_query_ids() == frozenset()
        assert leaked_ui_backends() == 0

    def test_pii_masked(self, runner: QueryRunner) -> None:
        sql_text = "select customer_id, email, first_name, last_name, country_code from marts.dim_customers"

        client = runner.run("q1", sql_text + " order by customer_id limit 3")
        server = runner.run("q2", sql_text, filters=[FilterSpec(column="email", operator="contains", value="@")])

        for outcome in (client, server):
            assert {row[1:4] for row in outcome.rows} == {("***", "***", "***")}
            assert all(isinstance(row[0], int) and row[4] != "***" for row in outcome.rows)

    def test_zero_rows_and_zero_columns(self, runner: QueryRunner) -> None:
        no_rows = runner.run("q1", "select order_id, order_total from marts.fct_orders where false")
        no_rows_server = runner.run(
            "q2",
            "select order_id from marts.fct_orders",
            filters=[FilterSpec(column="order_id", operator="is_null")],
        )
        no_columns = runner.run("q3", "select from marts.fct_orders limit 1")
        neither = runner.run("q4", "select from marts.fct_orders where false")

        assert (no_rows.columns, no_rows.column_types) == (("order_id", "order_total"), ("number", "number"))
        assert (no_rows.rows, no_rows.returned_rows, no_rows.truncated) == ((), 0, False)
        assert (no_rows_server.mode, no_rows_server.columns, no_rows_server.rows) == ("server", ("order_id",), ())
        assert (no_columns.columns, no_columns.column_types) == ((), ())
        assert (no_columns.rows, no_columns.returned_rows, no_columns.truncated) == (((),), 1, False)
        assert (neither.columns, neither.rows, neither.returned_rows) == ((), (), 0)

    def test_null_cells_stay_null(self, runner: QueryRunner) -> None:
        outcome = runner.run(
            "q1",
            "select null as a, null::int as b, null::numeric as c, null::date as d, null::text as email"
            " from marts.fct_orders limit 2",
        )

        assert outcome.rows == ((None, None, None, None, None),) * 2  # a NULL in a PII column is not masked either
        assert outcome.column_types == ("text", "number", "number", "date", "text")

    def test_column_types_reported(self, runner: QueryRunner) -> None:
        outcome = runner.run(
            "q1",
            "select order_id, order_number, order_date, placed_at, is_cancelled, order_total, 1.5::float8 as f,"
            " 2::int2 as s, 'x'::varchar(3) as v, 'y'::char(2) as c, interval '1 day' as i, '{}'::jsonb as j"
            " from marts.fct_orders limit 1",
        )

        assert outcome.column_types == (
            "number",
            "text",
            "date",
            "timestamp",
            "boolean",
            "number",
            "number",
            "number",
            "text",
            "text",
            "other",
            "other",
        )

    def test_values_are_json_safe(self, runner: QueryRunner) -> None:
        outcome = runner.run(
            "q1",
            "select 1.50::numeric(12,2) as d, date '2025-03-01' as day,"
            " timestamptz '2025-03-01 04:05:00+00' as ts, true as b, '{\"k\": [1]}'::jsonb as j",
        )

        assert outcome.rows == (("1.50", "2025-03-01", "2025-03-01T04:05:00+00:00", True, {"k": [1]}),)


def _fake_connection(rows: list[tuple[object, ...]] | None = None, columns: tuple[str, ...] = ("a",)) -> MagicMock:
    """A connection whose server-side cursor returns `rows` of int4 `columns`."""
    connection: MagicMock = create_autospec(psycopg.Connection, instance=True)
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.description = [SimpleNamespace(name=name, type_code=INT4_OID) for name in columns]
    cursor.fetchmany.return_value = rows or []
    return connection


def _describe(cause: BaseException | None) -> str:
    """A `UiQueryError` by its code, anything else by its type."""
    return cause.code if isinstance(cause, UiQueryError) else type(cause).__name__


def _must_not_connect(*args: object, **kwargs: object) -> None:
    pytest.fail("the runner connected to the warehouse")


def _wait_until_running(runner: QueryRunner, query_id: str) -> None:
    deadline = time.monotonic() + REGISTER_WAIT_SECONDS
    while query_id not in runner.running_query_ids():
        assert time.monotonic() < deadline, f"{query_id} never started running"
        time.sleep(0.01)
