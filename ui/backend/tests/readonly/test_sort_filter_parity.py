"""The shared sort/filter case file (`ui/parity/sort_filter_cases.json`, UI spec §5) against real Postgres: the file's
expected ids are what the server wrapper returns, and its tables are what the API returns in client mode. The
frontend's `resultViewParity.test.ts` runs the same file through the browser's code."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, get_args

import psycopg
import pytest
from pydantic import BaseModel, ConfigDict
from sherpa_mcp.masking import PiiMasker
from sherpa_ui.readonly.query_runner import COLUMN_TYPES, QueryRunner, UiQueryError
from sherpa_ui.readonly.sort_filter import FilterOperator, FilterSpec, SortSpec, build_wrapped_query
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sherpa_mcp.catalog import MartCatalogLoader
    from sherpa_ui.settings import UiSettings

CASE_FILE = REPO_ROOT / "ui" / "parity" / "sort_filter_cases.json"
QUERY_ID = "parity"


class ParityTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    sql: str
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]


class ParitySort(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    direction: Literal["asc", "desc"]


class ParityFilter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    operator: FilterOperator
    value: str | None = None


class ParityCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    table: str
    sort: ParitySort | None = None
    filters: list[ParityFilter] = []
    expected_ids: list[int] | None = None  # None: Postgres refuses the query
    expected_error: bool = False
    route: Literal["server"] | None = None
    accepted_difference: str | None = None
    client_expected_ids: list[int] | None = None


class ParityFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tables: list[ParityTable]
    cases: list[ParityCase]


PARITY = ParityFile.model_validate_json(CASE_FILE.read_text(encoding="utf-8"))
TABLES = {table.name: table for table in PARITY.tables}


@pytest.fixture
def values_runner(ui_settings: UiSettings, empty_catalog_loader: MartCatalogLoader) -> QueryRunner:
    """The tables are `values` lists, which touch no relation, so a catalog with no marts is enough."""
    return QueryRunner(ui_settings, empty_catalog_loader)


@pytest.fixture(scope="module")
def ui_connection(ui_settings: UiSettings) -> Iterator[psycopg.Connection]:
    """One read-only connection for the ~560 cases that run the wrapper: a connection per case costs ~20 s."""
    with psycopg.connect(ui_settings.ui_conninfo(), autocommit=True) as conn:
        yield conn


@pytest.mark.integration
@pytest.mark.parametrize("case", [case for case in PARITY.cases if not case.expected_error], ids=lambda case: case.name)
def test_every_case_matches_postgres(ui_connection: psycopg.Connection, case: ParityCase) -> None:
    table = TABLES[case.table]
    wrapped = build_wrapped_query(table.sql, table.columns, PiiMasker(frozenset()), *_specs(case, table))

    rows = ui_connection.execute(wrapped.query, wrapped.params).fetchall()

    assert [row[0] for row in rows] == case.expected_ids


@pytest.mark.integration
@pytest.mark.parametrize("case", [case for case in PARITY.cases if case.expected_error], ids=lambda case: case.name)
def test_a_value_postgres_refuses_is_a_sql_error(values_runner: QueryRunner, case: ParityCase) -> None:
    table = TABLES[case.table]

    with pytest.raises(UiQueryError) as error:
        values_runner.run(QUERY_ID, table.sql, *_specs(case, table))

    assert error.value.code == "sql_error"


@pytest.mark.integration
@pytest.mark.parametrize("table", PARITY.tables, ids=lambda table: table.name)
def test_the_tables_are_what_the_api_returns(values_runner: QueryRunner, table: ParityTable) -> None:
    outcome = values_runner.run(QUERY_ID, table.sql)

    body = outcome.model_dump(mode="json")
    assert body["mode"] == "client"
    assert body["columns"] == table.columns
    assert body["column_types"] == table.column_types
    # As JSON text, since Python's == hides the type: True == 1 and 2 == 2.0, but the browser tells them apart.
    assert json.dumps(body["rows"], ensure_ascii=False) == json.dumps(table.rows, ensure_ascii=False)


def test_the_case_file_covers_every_operator_and_direction_on_every_column_type() -> None:
    # Per grid type, not overall: an operator that only ever ran on text would leave the other types unchecked.
    grid_types = {*COLUMN_TYPES.values(), "other"}
    used: dict[str, set[str]] = {kind: set() for kind in grid_types}
    for case in PARITY.cases:
        types = TABLES[case.table].column_types
        for spec in case.filters:
            used[types[spec.index]].add(f"op {spec.operator}")

        if case.sort:
            used[types[case.sort.index]].add(f"sort {case.sort.direction}")

    every = {f"op {operator}" for operator in get_args(FilterOperator)} | {"sort asc", "sort desc"}
    assert {kind: sorted(every - what) for kind, what in used.items()} == {kind: [] for kind in grid_types}


def test_every_table_has_at_most_six_rows() -> None:
    # Postgres's sort is stable below 7 rows (insertion sort) and not above, while the browser's is always stable: a
    # larger table with tied keys would make the expected order depend on Postgres's quicksort.
    assert {table.name: len(table.rows) for table in PARITY.tables if len(table.rows) > 6} == {}


def test_an_accepted_difference_in_the_file_names_what_the_browser_answers() -> None:
    accepted = [case for case in PARITY.cases if case.accepted_difference]

    assert accepted
    # A new acceptance must change the spec's section 5 and this line.
    assert {case.table for case in accepted} == {"timestamps_naive"}
    assert all(
        case.client_expected_ids is not None and case.client_expected_ids != case.expected_ids for case in accepted
    )


def _specs(case: ParityCase, table: ParityTable) -> tuple[list[SortSpec], list[FilterSpec]]:
    sort = [SortSpec(column=table.columns[case.sort.index], direction=case.sort.direction)] if case.sort else []
    filters = [
        FilterSpec(column=table.columns[spec.index], operator=spec.operator, value=spec.value) for spec in case.filters
    ]
    return sort, filters
