from __future__ import annotations

import datetime as dt
import ipaddress
import json
import time
import uuid
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors
from sherpa_mcp.database import (
    AgentDatabase,
    QueryResult,
    WarehouseToolError,
    cap_cell,
    json_safe,
    to_tool_error,
)


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

    def test_small_values_pass_through(self) -> None:
        assert cap_cell(["a", "b"]) == ["a", "b"]
        assert cap_cell({"k": 1}) == {"k": 1}
        assert cap_cell(12) == 12
        assert cap_cell(None) is None
        assert cap_cell("") == ""


class TestErrorMapping:
    def test_timeout_message_suggests_narrowing(self) -> None:
        error = to_tool_error(errors.QueryCanceled("canceling statement due to statement timeout"))

        assert str(error) == "the query took longer than 5 s; aggregate or filter more narrowly"

    def test_data_exceptions_never_carry_the_value(self) -> None:
        error = to_tool_error(errors.InvalidTextRepresentation('invalid input syntax for type integer: "alice@x.com"'))

        assert "alice" not in str(error)
        assert "InvalidTextRepresentation" in str(error)
        assert "explicit, safe casts" in str(error)

    def test_syntax_and_column_errors_keep_postgres_message(self) -> None:
        assert "no_such" in str(to_tool_error(errors.UndefinedColumn('column "no_such" does not exist')))

    def test_other_errors_keep_postgres_message(self) -> None:
        error = to_tool_error(errors.InsufficientPrivilege("permission denied for table x"))

        assert "permission denied for table x" in str(error)

    def test_query_result_is_frozen(self) -> None:
        result = QueryResult(columns=("a",), rows=((1,),), returned_rows=1, truncated=False, elapsed_ms=1)

        with pytest.raises(AttributeError):
            result.truncated = True  # type: ignore[misc]


@pytest.mark.warehouse
class TestAgentDatabase:
    def test_session_is_read_only(self, agent_database: AgentDatabase) -> None:
        assert agent_database.scalar("select current_setting('transaction_read_only')") == "on"

    def test_fetch_caps_and_flags_truncation(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select * from marts.fct_order_items")

        assert result.returned_rows == 100
        assert len(result.rows) == 100
        assert result.truncated

    def test_fetch_exact_cap_is_not_truncated(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select * from marts.fct_order_items limit 100")

        assert result.returned_rows == 100
        assert not result.truncated

    def test_long_cells_are_cut_and_rows_keep_their_cap(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select lpad('a', 1000000, 'b') as n from marts.fct_order_items")

        assert result.returned_rows == 100
        assert result.truncated
        assert set(result.rows) == {("b" * 10_000 + " …[cut at 10000 chars]",)}
        assert len(json.dumps(result.rows)) < 1_100_000

    def test_hundred_megabyte_cell_is_cut(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch("select lpad('a', 100000000, 'b') as n from marts.fct_order_items limit 1")

        assert result.rows == (("b" * 10_000 + " …[cut at 10000 chars]",),)

    def test_fetching_huge_rows_stops_at_the_timeout(self, agent_database: AgentDatabase) -> None:
        started = time.monotonic()

        with pytest.raises(WarehouseToolError, match="took longer than 5 s"):
            agent_database.fetch("select lpad('a', 100000000, 'b') as n from marts.fct_order_items")

        assert time.monotonic() - started < 15
        assert agent_database.scalar("select 1") == 1

    def test_timeout_is_reported_and_connection_reusable(self, agent_database: AgentDatabase) -> None:
        started = time.monotonic()

        with pytest.raises(WarehouseToolError, match="took longer than 5 s"):
            agent_database.fetch("select pg_sleep(30)")

        assert time.monotonic() - started < 7
        assert agent_database.scalar("select 1") == 1

    def test_write_attempt_is_refused(self, agent_database: AgentDatabase, admin: psycopg.Connection) -> None:
        with pytest.raises(WarehouseToolError):
            agent_database.fetch("create table marts.x(i int)")

        assert admin.execute("select to_regclass('marts.x')").fetchone() == (None,)

    def test_unreadable_relation_is_refused_by_the_role(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError, match="permission denied"):
            agent_database.fetch("select * from staging.stg_shop__orders")

    def test_explain_rejects_analyze_prefix(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError):
            agent_database.explain("analyze select 1")
        with pytest.raises(WarehouseToolError):
            agent_database.explain("(analyze) select 1")

    def test_multiple_statements_are_rejected_by_postgres(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError):
            agent_database.fetch("select 1; select 2")

        assert agent_database.scalar("select 1") == 1

    def test_role_cannot_alter_itself(self, agent_database: AgentDatabase) -> None:
        with pytest.raises(WarehouseToolError):
            agent_database.fetch("alter role sherpa_agent set statement_timeout = 0")

    def test_values_are_json_safe(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch(
            "select 1.5::numeric as d, date '2025-03-01' as day, timestamptz '2025-03-01 04:05:00+00' as ts"
        )

        assert result.columns == ("d", "day", "ts")
        assert result.rows == (("1.5", "2025-03-01", "2025-03-01T04:05:00+00:00"),)

    def test_parameters_are_bound(self, agent_database: AgentDatabase) -> None:
        assert agent_database.scalar("select %s::int + 1", (41,)) == 42

    def test_explain_reports_postgres_message(self, agent_database: AgentDatabase) -> None:
        agent_database.explain("select 1")

        with pytest.raises(WarehouseToolError, match="does_not_exist"):
            agent_database.explain("select * from marts.does_not_exist")

    def test_similarity_threshold_is_set_on_request(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch(
            "select current_setting('pg_trgm.similarity_threshold')", set_similarity_threshold=True
        )

        assert result.rows == (("0.2",),)

    def test_arrays_intervals_and_nan_are_json_safe(self, agent_database: AgentDatabase) -> None:
        result = agent_database.fetch(
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
