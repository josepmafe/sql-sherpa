from __future__ import annotations

import datetime as dt
import ipaddress
import json
import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, create_autospec

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import SecretStr
from sherpa_mcp.database import (
    AgentDatabase,
    WarehouseToolError,
    cap_cell,
    cell_chars,
    json_safe,
    to_tool_error,
)
from sherpa_mcp.settings import McpSettings

CUT = " …[cut at 10000 chars]"


class TestJsonSafe:
    def test_decimal_becomes_string(self) -> None:
        assert json_safe(Decimal("12.50")) == "12.50"

    def test_dates_and_timestamps_become_iso_strings(self) -> None:
        assert json_safe(dt.date(2025, 3, 1)) == "2025-03-01"
        assert json_safe(dt.datetime(2025, 3, 1, 4, 5, tzinfo=dt.UTC)) == "2025-03-01T04:05:00+00:00"

    def test_other_scalar_types_become_strings(self) -> None:
        identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")

        assert json_safe(identifier) == str(identifier)
        assert json_safe(dt.timedelta(days=2, hours=3)) == "2 days, 3:00:00"
        assert json_safe(ipaddress.ip_address("10.0.0.1")) == "10.0.0.1"
        assert json_safe(ipaddress.ip_network("10.0.0.0/8")) == "10.0.0.0/8"
        assert json_safe(b"\x00\xff") == "00ff"

    def test_non_finite_floats_become_strings(self) -> None:
        assert json_safe(float("nan")) == "nan"
        assert json_safe(float("inf")) == "inf"
        assert json_safe(1.5) == 1.5

    def test_containers_are_converted_recursively(self) -> None:
        value = {"a": [Decimal("1.0"), dt.date(2025, 1, 2)], "b": (uuid.UUID(int=1),)}

        converted = json_safe(value)

        assert converted == {"a": ["1.0", "2025-01-02"], "b": ["00000000-0000-0000-0000-000000000001"]}
        json.dumps(converted, allow_nan=False)

    def test_plain_values_pass_through(self) -> None:
        assert json_safe(7) == 7
        assert json_safe(None) is None
        assert json_safe("x") == "x"


class TestCapCell:
    def test_text_at_the_cap_is_kept(self) -> None:
        assert cap_cell("a" * 10_000) == "a" * 10_000

    def test_longer_text_is_cut_with_a_visible_marker(self) -> None:
        capped = cap_cell("a" * 10_001)

        assert capped == "a" * 10_000 + " …[cut at 10000 chars]"

    def test_bytes_are_capped_after_hex_conversion(self) -> None:
        capped = cap_cell(json_safe(b"\xff" * 5_001))  # 10,002 hex characters

        assert capped == "f" * 10_000 + " …[cut at 10000 chars]"

    def test_large_array_or_document_becomes_its_cut_json_text(self) -> None:
        array = cap_cell(["x" * 6_000, "y" * 6_000])
        document = cap_cell({"k": "z" * 20_000})

        assert isinstance(array, str)
        assert array == ('["' + "x" * 6_000 + '", "' + "y" * 6_000)[:10_000] + " …[cut at 10000 chars]"
        assert isinstance(document, str)
        assert document.startswith('{"k": "zzz')
        assert document.endswith(" …[cut at 10000 chars]")

    def test_non_ascii_text_in_a_document_counts_as_one_character_each(self) -> None:
        document = {"k": "é" * 9_991}  # exactly 10,000 characters as JSON text; 59,955 with \u escapes

        assert cap_cell(document) == document
        assert cap_cell({"k": "é" * 9_992}) == '{"k": "' + "é" * 9_992 + '"' + CUT

    def test_small_values_pass_through(self) -> None:
        assert cap_cell(["a", "b"]) == ["a", "b"]
        assert cap_cell({"k": 1}) == {"k": 1}
        assert cap_cell(12) == 12
        assert cap_cell(None) is None
        assert cap_cell("") == ""


class TestCellChars:
    def test_text_counts_its_characters(self) -> None:
        assert cell_chars("") == 0
        assert cell_chars("é" * 3) == 3

    def test_arrays_and_documents_count_their_json_text(self) -> None:
        assert cell_chars(["a", "é"]) == len('["a", "é"]')
        assert cell_chars({"k": 1}) == len('{"k": 1}')

    def test_other_values_count_their_text(self) -> None:
        assert cell_chars(None) == 4
        assert cell_chars(12345) == 5
        assert cell_chars(True) == 4


class TestErrorMapping:
    def test_timeout_message_suggests_narrowing(self) -> None:
        error = to_tool_error(errors.QueryCanceled("canceling statement due to statement timeout"))

        assert str(error) == "[timeout] the query took longer than 5 s; aggregate or filter more narrowly"

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (
                errors.InvalidTextRepresentation('invalid input syntax for type integer: "alice@x.com"'),
                "[sql] a value could not be converted or computed (InvalidTextRepresentation); "
                "check the literal format",
            ),
            (
                errors.DivisionByZero("division by zero"),
                "[sql] a value could not be converted or computed (DivisionByZero); "
                "wrap the denominator in nullif(x, 0)",
            ),
            (
                errors.InvalidDatetimeFormat('invalid input syntax for type date: "alice@x.com"'),
                "[sql] a value could not be converted or computed (InvalidDatetimeFormat); check the literal format",
            ),
            (
                errors.DatetimeFieldOverflow('date/time field value out of range: "alice@x.com"'),
                "[sql] a value could not be converted or computed (DatetimeFieldOverflow); check the literal format",
            ),
            (
                errors.NumericValueOutOfRange('value "alice@x.com" is out of range'),
                "[sql] a value could not be converted or computed (NumericValueOutOfRange); "
                "check the column types and use explicit, safe casts",
            ),
        ],
    )
    def test_data_exceptions_never_carry_the_value(self, error: psycopg.Error, expected: str) -> None:
        assert str(to_tool_error(error)) == expected

    @pytest.mark.parametrize(
        "error",
        [
            errors.ConnectionFailure("connection to server failed"),
            errors.SqlclientUnableToEstablishSqlconnection("could not connect"),
            psycopg.OperationalError("connection failed: server closed the connection unexpectedly"),
            PoolTimeout("couldn't get a connection after 10.00 sec"),
        ],
    )
    def test_lost_connections_are_unreachable(self, error: psycopg.Error) -> None:
        assert str(to_tool_error(error)) == "[unreachable] the warehouse is unreachable; is `make up` running?"

    def test_syntax_and_column_errors_keep_postgres_message(self) -> None:
        error = to_tool_error(errors.UndefinedColumn('column "no_such" does not exist'))

        assert str(error) == '[sql] column "no_such" does not exist'

    def test_other_errors_keep_postgres_message(self) -> None:
        error = to_tool_error(errors.InsufficientPrivilege("permission denied for table x"))

        assert str(error) == "[sql] permission denied for table x"


class TestSessionOptions:
    def test_similarity_thresholds_are_set_locally_to_the_specs_values(self) -> None:
        conn = MagicMock()

        AgentDatabase._begin(conn, set_similarity_threshold=True)

        statements = [item.args[0].as_string() for item in conn.execute.call_args_list]
        assert statements == [
            "set local statement_timeout = '5s'",
            "set local pg_trgm.similarity_threshold = '0.2'",
            "set local pg_trgm.word_similarity_threshold = '0.6'",
        ]

    def test_thresholds_are_left_alone_unless_requested(self) -> None:
        conn = MagicMock()

        AgentDatabase._begin(conn, set_similarity_threshold=False)

        statements = [item.args[0].as_string() for item in conn.execute.call_args_list]
        assert statements == ["set local statement_timeout = '5s'"]


class TestStartup:
    def test_unreachable_database_fails_the_wait(self, closed_port: int) -> None:
        settings = McpSettings(
            _env_file=None,  # type: ignore[call-arg]
            db_host="127.0.0.1",
            db_port=closed_port,
            db_name="sherpa",
            agent_user="sherpa_agent",
            agent_password=SecretStr("x"),
        )
        database = AgentDatabase(settings)
        try:
            started = time.monotonic()
            with pytest.raises(WarehouseToolError, match=r"^\[unreachable\] the warehouse is unreachable"):
                database.wait_until_connected(0.5)
            waited = time.monotonic() - started
        finally:
            database.close()

        # psycopg_pool closes the pool on a failed wait, which waits for the attempt in flight (a refused connection
        # takes ~2 s on Windows).
        assert waited < 10


@pytest.fixture
def fetch_sizes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The row count each FETCH asks the server-side cursor for."""
    sizes: list[int] = []
    fetchmany = psycopg.ServerCursor.fetchmany

    def recording(cursor: psycopg.ServerCursor[object], size: int = 0) -> list[object]:
        sizes.append(size)
        return fetchmany(cursor, size)

    monkeypatch.setattr(psycopg.ServerCursor, "fetchmany", recording)
    return sizes


class TestPoolWaits:
    """The waits for a connection and for a fetch slot, with no database: the pool is a stand-in."""

    @pytest.fixture
    def offline_database(self, closed_port: int, monkeypatch: pytest.MonkeyPatch) -> tuple[AgentDatabase, MagicMock]:
        pool_class = create_autospec(ConnectionPool)
        monkeypatch.setattr("sherpa_mcp.database.ConnectionPool", pool_class)
        settings = McpSettings(
            _env_file=None,  # type: ignore[call-arg]
            db_host="127.0.0.1",
            db_port=closed_port,
            db_name="sherpa",
            agent_user="sherpa_agent",
            agent_password=SecretStr("x"),
        )
        return AgentDatabase(settings), pool_class

    def test_the_wait_for_a_connection_is_10_seconds(self, offline_database: tuple[AgentDatabase, MagicMock]) -> None:
        _, pool_class = offline_database

        assert pool_class.call_args.kwargs["timeout"] == 10.0

    def test_the_wait_for_a_fetch_slot_is_10_seconds(self, offline_database: tuple[AgentDatabase, MagicMock]) -> None:
        database, _ = offline_database
        slots = MagicMock()
        slots.acquire.return_value = False
        database._heavy_fetches = slots

        with pytest.raises(WarehouseToolError, match="busy with other queries"):
            database.fetch_for_model("select 1")

        slots.acquire.assert_called_once_with(timeout=10.0)
        slots.release.assert_not_called()


@pytest.mark.integration
class TestAgentDatabaseOnPostgres:
    """Needs Postgres and the agent role only, not the built marts."""

    def test_set_local_timeout_is_enforced_on_its_own(
        self, warehouse_settings: McpSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The session default is made 60 s, so only `set local` can make the timeout 5 s.
        monkeypatch.setattr(
            "sherpa_mcp.settings.AGENT_SESSION_OPTIONS", "-c default_transaction_read_only=on -c statement_timeout=60s"
        )
        with psycopg.connect(warehouse_settings.agent_conninfo()) as raw:
            assert raw.execute("show statement_timeout").fetchone() == ("1min",)
        database = AgentDatabase(warehouse_settings)
        seen: list[object] = []
        begin = AgentDatabase._begin

        def recording(conn: psycopg.Connection, *, set_similarity_threshold: bool = False) -> None:
            begin(conn, set_similarity_threshold=set_similarity_threshold)
            seen.append(conn.execute("select current_setting('statement_timeout')").fetchone())

        monkeypatch.setattr(AgentDatabase, "_begin", staticmethod(recording))
        try:
            fetched = database.fetch("select current_setting('statement_timeout')")
            database.explain("select 1")
        finally:
            database.close()

        assert fetched.rows == (("5s",),)
        assert seen == [("5s",), ("5s",)]  # inside fetch, then inside explain

    def test_session_is_read_only(self, agent_database: AgentDatabase) -> None:
        # The setting comes from the connection's `-c` option, not from a role or database default.
        assert agent_database.scalar("select current_setting('transaction_read_only')") == "on"
        assert (
            agent_database.scalar("select source from pg_settings where name = 'default_transaction_read_only'")
            == "client"
        )

    def test_fetch_caps_and_flags_truncation(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select n from generate_series(1, 101) n")

        assert result.returned_rows == 100
        assert len(result.rows) == 100
        assert result.truncated

    def test_fetch_exact_cap_is_not_truncated(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select n from generate_series(1, 100) n")

        assert result.returned_rows == 100
        assert not result.truncated

    def test_fetch_returns_values_as_psycopg_loads_them(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select 1.5::numeric, date '2025-03-01', lpad('a', 20000, 'b')")

        assert result.rows == ((Decimal("1.5"), dt.date(2025, 3, 1), "b" * 19_999 + "a"),)

    def test_long_cells_are_cut_and_the_result_stops_at_its_budget(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch_for_model("select lpad('a', 1000000, 'b') as n from generate_series(1, 10)")

        # 10,022 characters a row: four rows fit in 50,000, a fifth would not.
        assert result.returned_rows == 4
        assert result.truncated
        assert set(result.rows) == {("b" * 10_000 + CUT,)}

    def test_rows_that_fill_the_budget_exactly_are_not_truncated(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch_for_model("select lpad('a', 10000, 'b') as n from generate_series(1, 5)")
        over = agent_database.fetch_for_model("select lpad('a', 10000, 'b') as n from generate_series(1, 6)")

        assert result.returned_rows == 5
        assert not result.truncated
        assert over.returned_rows == 5
        assert over.truncated

    def test_a_row_of_exactly_50000_characters_is_kept_and_one_of_50001_is_truncated(
        self, agent_database: AgentDatabase
    ) -> None:
        full = ", ".join(f"lpad('a', 10000, 'b') as c{index}" for index in range(5))

        at_budget = agent_database.fetch_for_model(f"select {full}")
        over_budget = agent_database.fetch_for_model(f"select {full}, 'x' as extra")

        assert at_budget.returned_rows == 1
        assert not at_budget.truncated
        assert over_budget.rows == ()
        assert over_budget.truncated

    def test_a_first_row_over_the_budget_returns_no_rows(self, agent_database: AgentDatabase) -> None:
        columns = ", ".join(f"lpad('a', 10000, 'b') as c{index}" for index in range(6))

        result = agent_database.fetch_for_model(f"select {columns}")

        assert result.rows == ()
        assert result.truncated

    def test_a_two_million_character_cell_is_cut(self, agent_database: AgentDatabase) -> None:
        # Above the 1,000,000-character large-row threshold and far past the 10,000-character cell cap.
        result = agent_database.fetch_for_model("select lpad('a', 2000000, 'b') as n")

        assert result.rows == (("b" * 10_000 + CUT,),)

    def test_rows_come_in_batches_and_one_past_the_cap(
        self, agent_database: AgentDatabase, fetch_sizes: list[int]
    ) -> None:
        agent_database.fetch_for_model("select n from generate_series(1, 101) n")
        capped = list(fetch_sizes)
        fetch_sizes.clear()
        agent_database.fetch_for_model("select n from generate_series(1, 45) n")

        assert capped == [20, 20, 20, 20, 20, 1]
        assert fetch_sizes == [20, 20, 20, 20]

    def test_after_a_large_row_the_rest_come_one_at_a_time(
        self, agent_database: AgentDatabase, fetch_sizes: list[int]
    ) -> None:
        result = agent_database.fetch_for_model(
            "select case when n = 1 then lpad('a', 1000001, 'b') else 'x' end as v from generate_series(1, 30) n"
        )

        assert result.returned_rows == 30
        assert fetch_sizes == [20, *[1] * 11]  # 10 more rows, then the empty fetch that ends the result

    def test_deeply_nested_value_is_a_tool_error(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError, match=r"^\[sql\] a value is nested too deeply"):
            agent_database.fetch_for_model("select (repeat('[', 5000) || repeat(']', 5000))::jsonb")

        assert agent_database.scalar("select 1") == 1

    def test_heavy_fetches_wait_for_a_free_slot(
        self, agent_database: AgentDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sherpa_mcp.database.HEAVY_FETCH_WAIT_SECONDS", 0.1)
        slots = agent_database._heavy_fetches  # both slots taken, as by two running queries
        slots.acquire()
        slots.acquire()
        try:
            with pytest.raises(WarehouseToolError, match=r"^\[timeout\] the server is busy with other queries"):
                agent_database.fetch_for_model("select 1")
        finally:
            slots.release()
            slots.release()

        assert agent_database.fetch_for_model("select 1").rows == ((1,),)

    def test_fetching_stops_between_5_and_7_seconds(self, agent_database: AgentDatabase) -> None:
        started = time.monotonic()

        # Each FETCH of 20 rows sleeps 1.75 s, well inside the statement timeout, so only the fetch deadline stops it:
        # the check before the 4th FETCH (at 5.25 s) is the first past 5 s. A 6 s deadline would stop it at 7 s.
        with pytest.raises(WarehouseToolError, match=r"^\[timeout\] the query took longer than 5 s"):
            agent_database.fetch_for_model("select pg_sleep(0.0875), n from generate_series(1, 1000) n")
        elapsed = time.monotonic() - started

        assert 5 <= elapsed < 7
        assert agent_database.scalar("select 1") == 1

    def test_timeout_is_reported_and_connection_reusable(
        self, agent_database: AgentDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The 5 s value itself is pinned by test_set_local_timeout_is_enforced_on_its_own; a short one keeps this fast.
        monkeypatch.setattr("sherpa_mcp.database.STATEMENT_TIMEOUT", "200ms")
        started = time.monotonic()

        with pytest.raises(WarehouseToolError, match=r"^\[timeout\] the query took longer than 5 s"):
            agent_database.fetch("select pg_sleep(30)")

        assert time.monotonic() - started < 3
        assert agent_database.scalar("select 1") == 1

    def test_a_write_is_refused_by_the_read_only_session(self, agent_database: AgentDatabase) -> None:
        # `for update` is valid inside DECLARE CURSOR, so the refusal comes from the read-only transaction.
        with pytest.raises(WarehouseToolError) as error:
            agent_database.fetch("select * from pg_class limit 1 for update")

        assert str(error.value) == "[sql] cannot execute SELECT FOR UPDATE in a read-only transaction"

    def test_explain_refuses_several_statements(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError) as error:
            agent_database.explain("select 1; select 2")

        assert str(error.value) == "[sql] cannot insert multiple commands into a prepared statement"

    def test_multiple_statements_are_rejected_by_postgres(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError) as error:
            agent_database.fetch("select 1; select 2")

        assert str(error.value) == "[sql] cannot insert multiple commands into a prepared statement"
        assert agent_database.scalar("select 1") == 1

    def test_explain_plans_without_running_the_query(self, agent_database: AgentDatabase) -> None:
        # Running this query would raise DivisionByZero.
        agent_database.explain("select 1 / (n - n) from generate_series(1, 1) n")

    def test_explain_rejects_analyze_prefix(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError, match=r"^\[sql\] "):
            agent_database.explain("analyze select 1")
        with pytest.raises(WarehouseToolError, match=r"^\[sql\] "):
            agent_database.explain("(analyze) select 1")

    def test_explain_reports_postgres_message(self, agent_database: AgentDatabase) -> None:
        agent_database.explain("select 1")

        with pytest.raises(WarehouseToolError, match=r"^\[sql\] .*does_not_exist"):
            agent_database.explain("select * from pg_catalog.does_not_exist")

    def test_values_are_json_safe(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch_for_model(
            "select 1.5::numeric as d, date '2025-03-01' as day, timestamptz '2025-03-01 04:05:00+00' as ts"
        )

        assert result.columns == ("d", "day", "ts")
        assert result.rows == (("1.5", "2025-03-01", "2025-03-01T04:05:00+00:00"),)

    def test_arrays_intervals_and_nan_are_json_safe(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch_for_model(
            "select array_agg(d) as days, date '2025-03-05' - date '2025-03-01' as gap,"
            " age(timestamp '2025-03-05', timestamp '2025-03-01') as span, 'NaN'::float8 as nan,"
            " '{\"k\": [1.5]}'::jsonb as doc, '\\xdead'::bytea as raw"
            " from (values (date '2025-01-01'), (date '2025-01-02')) as t(d)"
        )

        row = dict(zip(result.columns, result.rows[0], strict=True))
        assert row["days"] == ["2025-01-01", "2025-01-02"]
        assert row["gap"] == 4  # date minus date is an integer
        assert row["span"] == "4 days, 0:00:00"
        assert row["nan"] == "nan"
        assert row["doc"] == {"k": [1.5]}
        assert row["raw"] == "dead"
        json.dumps(result.rows, allow_nan=False)

    def test_division_by_zero_gets_its_remedy(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError) as error:
            agent_database.fetch_for_model("select count(*) / 0 from generate_series(1, 3)")

        assert str(error.value) == (
            "[sql] a value could not be converted or computed (DivisionByZero); wrap the denominator in nullif(x, 0)"
        )

    def test_similarity_thresholds_are_set_on_request(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch(
            "select current_setting('pg_trgm.similarity_threshold'), "
            "current_setting('pg_trgm.word_similarity_threshold')",
            set_similarity_threshold=True,
        )

        assert result.rows == (("0.2", "0.6"),)

    def test_pool_size_comes_from_the_settings(self, warehouse_settings: McpSettings) -> None:
        database = AgentDatabase(warehouse_settings.model_copy(update={"mcp_pool_size": 2}))
        try:
            assert database._pool.max_size == 2
        finally:
            database.close()

    def test_a_connection_postgres_dropped_is_replaced(self, warehouse_settings: McpSettings) -> None:
        database = AgentDatabase(warehouse_settings.model_copy(update={"mcp_pool_size": 1}))
        try:
            pid = database.scalar("select pg_backend_pid()")
            # The agent role may end its own sessions; this one is the pool's only connection.
            with psycopg.connect(warehouse_settings.agent_conninfo(), autocommit=True) as other:
                assert other.execute("select pg_terminate_backend(%s)", (pid,)).fetchone() == (True,)

            assert database.scalar("select 1") == 1
            assert database.scalar("select pg_backend_pid()") != pid
        finally:
            database.close()


@pytest.mark.warehouse
class TestAgentDatabase:
    """What needs the built marts: their grants, their columns and their types."""

    def test_the_role_has_no_write_privilege_even_in_a_read_write_session(self, agent_database: AgentDatabase) -> None:
        # A session can switch itself to read write (the role setting is only a default); the grants still refuse.
        with agent_database._pool.connection() as conn:
            conn.execute("set transaction read write")
            with pytest.raises(errors.InsufficientPrivilege) as locked:
                conn.execute("select * from marts.fct_orders limit 1 for update")
            conn.rollback()
            conn.execute("set transaction read write")
            with pytest.raises(errors.InsufficientPrivilege) as created:
                conn.execute("create table marts.x(i int)")
            conn.rollback()

        assert str(locked.value).startswith("permission denied for table fct_orders")
        assert str(created.value).startswith("permission denied for schema marts")

    def test_unreadable_relation_is_refused_by_the_role(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError, match=r"^\[sql\] permission denied"):
            agent_database.fetch("select * from staging.stg_shop__orders")

    def test_errors_point_into_the_query_as_written(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError) as fetched:
            agent_database.fetch_for_model("select nope from marts.fct_orders")
        with pytest.raises(WarehouseToolError) as explained:
            agent_database.explain("select nope from marts.fct_orders")

        assert str(fetched.value) == '[sql] column "nope" does not exist (at character 8)'
        assert str(explained.value) == '[sql] column "nope" does not exist (at character 8)'

    def test_postgres_hint_is_appended(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError) as error:
            agent_database.fetch_for_model("select c.emial from marts.dim_customers c")

        assert str(error.value) == (
            '[sql] column c.emial does not exist (at character 8); Perhaps you meant to reference the column "c.email".'
        )

    def test_result_carries_postgres_column_types(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch_for_model(
            "select order_id, net_revenue, net_revenue / 2 as half, order_date, sales_channel, array[1] as ids "
            "from marts.fct_orders order by order_id limit 1"
        )

        assert result.column_types == ("int8", "numeric(12,2)", "numeric", "date", "text", "int4[]")
