"""The shared sort/filter case file (`ui/parity/sort_filter_cases.json`, UI spec §5) against real Postgres: the file's
expected ids are what the server wrapper returns, and its tables are what the API returns in client mode. The
frontend's `resultViewParity.test.ts` runs the same file through the browser's code."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, get_args

import pytest
from pydantic import BaseModel, ConfigDict
from sherpa_ui.readonly.query_runner import COLUMN_TYPES, QueryRunner, UiQueryError
from sherpa_ui.readonly.sort_filter import FilterOperator, FilterSpec, SortSpec
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
    from sherpa_mcp.catalog import MartCatalogLoader
    from sherpa_ui.settings import UiSettings

CASE_FILE = REPO_ROOT / "ui" / "parity" / "sort_filter_cases.json"
QUERY_ID = "parity"


class ParityTable(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    sql: str
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]


class ParitySort(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    direction: Literal["asc", "desc"]


class ParityFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    operator: FilterOperator
    value: str | None = None


class ParityCase(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    tables: list[ParityTable]
    cases: list[ParityCase]


PARITY = ParityFile.model_validate_json(CASE_FILE.read_text(encoding="utf-8"))
TABLES = {table.name: table for table in PARITY.tables}


@pytest.fixture
def values_runner(ui_settings: UiSettings, empty_catalog_loader: MartCatalogLoader) -> QueryRunner:
    """The tables are `values` lists, which touch no relation, so a catalog with no marts is enough."""
    return QueryRunner(ui_settings, empty_catalog_loader)


@pytest.mark.integration
@pytest.mark.parametrize("case", PARITY.cases, ids=lambda case: case.name)
def test_every_case_matches_postgres(values_runner: QueryRunner, case: ParityCase) -> None:
    table = TABLES[case.table]
    sort = [SortSpec(column=table.columns[case.sort.index], direction=case.sort.direction)] if case.sort else []
    filters = [
        FilterSpec(column=table.columns[spec.index], operator=spec.operator, value=spec.value) for spec in case.filters
    ]

    if case.expected_error:
        with pytest.raises(UiQueryError) as error:
            values_runner.run(QUERY_ID, table.sql, sort, filters)

        assert error.value.code == "sql_error"
        return

    outcome = values_runner.run(QUERY_ID, table.sql, sort, filters)

    assert outcome.mode == "server"
    assert [row[0] for row in outcome.rows] == case.expected_ids


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


def test_a_table_with_tied_keys_has_at_most_six_rows() -> None:
    # Postgres's sort is stable below 7 rows (insertion sort) and not above, while the browser's is always stable: a
    # larger table with ties would make the expected order depend on Postgres's quicksort.
    for table in PARITY.tables:
        keys = [json.dumps(row[1]) for row in table.rows if row[1] is not None]
        nulls = sum(row[1] is None for row in table.rows)
        has_ties = len(set(keys)) != len(keys) or nulls > 1

        assert not has_ties or len(table.rows) <= 6, table.name


def test_an_accepted_difference_in_the_file_names_what_the_browser_answers() -> None:
    accepted = [case for case in PARITY.cases if case.accepted_difference]

    assert accepted
    assert all(
        case.client_expected_ids is not None and case.client_expected_ids != case.expected_ids for case in accepted
    )
