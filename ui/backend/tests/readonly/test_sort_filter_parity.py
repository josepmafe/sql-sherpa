"""The shared sort/filter case file (`ui/parity/sort_filter_cases.json`, UI spec §5) against real Postgres: the file's
expected ids are what the server wrapper returns, and its tables are what the API returns in client mode. The
frontend's `resultViewParity.test.ts` runs the same file through the browser's code."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, get_args
from unittest.mock import create_autospec

import pytest
from pydantic import BaseModel, ConfigDict
from sherpa_mcp.catalog import MartCatalog, MartCatalogLoader
from sherpa_ui.readonly.query_runner import COLUMN_TYPES, QueryRunner, UiQueryError
from sherpa_ui.readonly.sort_filter import FilterOperator, FilterSpec, SortSpec
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
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


@pytest.fixture(scope="module")
def values_runner(ui_settings: UiSettings) -> QueryRunner:
    """A runner whose catalog has no marts: the tables are `values` lists, which touch no relation."""
    loader = create_autospec(MartCatalogLoader, instance=True)
    loader.load.return_value = MartCatalog(marts=(), metrics=(), generated_at=datetime(2026, 10, 1, tzinfo=UTC))
    return QueryRunner(ui_settings, loader)


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
    assert body["rows"] == table.rows


def test_the_case_file_covers_every_operator_and_column_type() -> None:
    operators = {spec.operator for case in PARITY.cases for spec in case.filters}
    types = {kind for table in PARITY.tables for kind in table.column_types}
    directions = {case.sort.direction for case in PARITY.cases if case.sort}

    assert operators == set(get_args(FilterOperator))
    assert types == {*COLUMN_TYPES.values(), "other"}
    assert directions == {"asc", "desc"}


def test_an_accepted_difference_records_what_the_browser_answers() -> None:
    accepted = [case for case in PARITY.cases if case.accepted_difference]

    assert accepted
    assert all(
        case.client_expected_ids is not None and case.client_expected_ids != case.expected_ids for case in accepted
    )
