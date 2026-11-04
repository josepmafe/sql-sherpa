from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import create_autospec

import pytest
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable
from sherpa_ui.home.data_status import (
    BUILD_UNAVAILABLE_NOTE,
    FRESHNESS_UNAVAILABLE_NOTE,
    NO_BUILD_NOTE,
    NO_FRESHNESS_NOTE,
    Freshness,
    FreshnessReader,
    FreshnessUnavailable,
)
from sherpa_ui.home.home_queries import HOME_QUERIES_PATH, HomeQueries
from sherpa_ui.home.home_service import NO_DATA_MESSAGE, HomeService
from sherpa_ui.readonly.query_runner import QueryOutcome, QueryRunner, UiQueryError
from ui_helpers import STARTED_AT, make_last_full_build, make_node_result

AS_OF = "2026-10-31"  # a Saturday
FIRST_ORDER = "2025-10-27"
FRESHNESS = Freshness(checked_at=STARTED_AT, problems=())
# this_period, last_period per tile; they differ per tile so a mix-up shows
TILE_ROWS = {
    "net_revenue": ("34811.50", "31000.00"),
    "orders": ("410", "400"),
    "aov": ("84.90", "77.50"),
    "refund_rate": ("0.0450", "0.0400"),
    "new_customers": ("95", "120"),
}
TREND_ROWS = (("2026-10-19", "20000.00", False), ("2026-10-26", "8000.50", True))


def outcome(columns: tuple[str, ...], *rows: tuple[object, ...]) -> QueryOutcome:
    return QueryOutcome.model_validate(
        {
            "query_id": "q",
            "columns": columns,
            "column_types": ("other",) * len(columns),
            "rows": rows,
            "returned_rows": len(rows),
            "truncated": False,
            "elapsed_ms": 1,
            "mode": "client",
        }
    )


def period(as_of: str | None = AS_OF, first: str | None = FIRST_ORDER) -> QueryOutcome:
    return outcome(("as_of_date", "first_order_date"), (as_of, first))


def answers(queries: HomeQueries, period_outcome: QueryOutcome) -> list[QueryOutcome]:
    tiles = [outcome(("this_period", "last_period"), TILE_ROWS[tile.id]) for tile in queries.tiles]
    trend = outcome(("week_start", "net_revenue", "is_black_friday_week"), *TREND_ROWS)
    return [period_outcome, *tiles, trend]


@pytest.fixture(scope="module")
def queries() -> HomeQueries:
    return HomeQueries.from_yaml(HOME_QUERIES_PATH)


@pytest.fixture
def runner(queries: HomeQueries) -> QueryRunner:
    fake = create_autospec(QueryRunner, instance=True)
    fake.run.side_effect = answers(queries, period())
    return fake


@pytest.fixture
def builds() -> BuildStatusReader:
    fake = create_autospec(BuildStatusReader, instance=True)
    fake.last_full_build.return_value = make_last_full_build(
        make_node_result("model.p.a", "success"),
        make_node_result("model.p.b", "error"),
        make_node_result("test.p.t1", "fail"),
        make_node_result("test.p.t2", "pass"),
    )
    return fake


@pytest.fixture
def freshness() -> FreshnessReader:
    fake = create_autospec(FreshnessReader, instance=True)
    fake.read.return_value = FRESHNESS
    return fake


@pytest.fixture
def service(
    runner: QueryRunner, builds: BuildStatusReader, freshness: FreshnessReader, queries: HomeQueries
) -> HomeService:
    return HomeService(runner, builds, freshness, queries)


class TestHome:
    def test_happy_path_has_five_tiles_a_trend_and_the_status(self, service: HomeService) -> None:
        home = service.home()

        assert home.figures_error is None
        assert home.figures is not None
        assert [t.id for t in home.figures.tiles] == ["net_revenue", "orders", "aov", "refund_rate", "new_customers"]
        net_revenue = home.figures.tiles[0]
        assert (net_revenue.value, net_revenue.direction, net_revenue.good) == (Decimal("34811.50"), "up", True)
        assert home.figures.tiles[3].change == Decimal("0.0050")
        assert home.figures.tiles[4].good is False
        assert [(w.week_start, w.is_partial, w.is_black_friday_week) for w in home.figures.trend] == [
            (date(2026, 10, 19), False, False),
            (date(2026, 10, 26), True, True),
        ]
        assert home.figures.trend[1].net_revenue == Decimal("8000.50")
        assert home.status.as_of_date == date(2026, 10, 31)
        assert home.status.build is not None
        assert (home.status.build.node_count, home.status.build.failing_tests, home.status.build.failed_nodes) == (
            4,
            1,
            1,
        )
        assert home.status.build.started_at == STARTED_AT
        assert home.status.build_note is None
        assert home.status.freshness == FRESHNESS
        assert home.status.freshness_note is None

    def test_a_history_shorter_than_two_windows_shows_values_without_changes(
        self, runner: QueryRunner, queries: HomeQueries, service: HomeService
    ) -> None:
        runner.run.side_effect = answers(queries, period(first="2026-09-03"))  # 58 days before as_of

        home = service.home()

        assert home.figures is not None
        assert all(t.value is not None and t.change is None for t in home.figures.tiles)

    def test_queries_run_in_order_with_distinct_home_ids(
        self, service: HomeService, runner: QueryRunner, queries: HomeQueries
    ) -> None:
        service.home()

        calls = runner.run.call_args_list
        assert [c.args[1] for c in calls] == [queries.period, *(t.sql for t in queries.tiles), queries.trend]
        ids = [c.args[0] for c in calls]
        assert len(set(ids)) == len(ids)
        assert all(query_id.startswith("home-") for query_id in ids)

    def test_no_orders_gives_no_data_and_runs_nothing_else(self, service: HomeService, runner: QueryRunner) -> None:
        runner.run.side_effect = [period(as_of=None, first=None)]

        home = service.home()

        assert runner.run.call_count == 1
        assert home.figures is None
        assert home.figures_error is not None
        assert (home.figures_error.code, home.figures_error.message) == ("no_data", NO_DATA_MESSAGE)
        assert home.status.as_of_date is None

    def test_failing_period_keeps_the_freshness(self, service: HomeService, runner: QueryRunner) -> None:
        runner.run.side_effect = UiQueryError("database_unavailable", "the warehouse is unreachable")

        home = service.home()

        assert runner.run.call_count == 1
        assert home.figures is None
        assert home.figures_error is not None
        assert (home.figures_error.code, home.figures_error.message) == (
            "database_unavailable",
            "the warehouse is unreachable",
        )
        assert home.status.as_of_date is None
        assert home.status.freshness == FRESHNESS

    def test_missing_marts_give_the_empty_state(self, service: HomeService, runner: QueryRunner) -> None:
        runner.run.side_effect = UiQueryError(
            "sql_error", 'relation "marts.fct_orders" does not exist', sqlstate="42P01"
        )

        home = service.home()

        assert home.figures is None
        assert home.figures_error is not None
        assert (home.figures_error.code, home.figures_error.message) == ("no_data", NO_DATA_MESSAGE)
        assert home.status.freshness == FRESHNESS

    def test_another_sql_error_keeps_its_own_code(self, service: HomeService, runner: QueryRunner) -> None:
        runner.run.side_effect = UiQueryError("sql_error", "permission denied", sqlstate="42501")

        home = service.home()

        assert home.figures_error is not None
        assert (home.figures_error.code, home.figures_error.message) == ("sql_error", "permission denied")

    def test_no_data_message_names_the_way_out(self) -> None:
        assert NO_DATA_MESSAGE == (
            "no orders in the marts yet: run make build-prod "
            "(first make backfill if uv run sherpa-generate status shows no simulation)"
        )

    def test_a_failing_tile_stops_the_queries_after_it(
        self, service: HomeService, runner: QueryRunner, queries: HomeQueries
    ) -> None:
        done = answers(queries, period())
        runner.run.side_effect = [*done[:3], UiQueryError("timeout", "too slow"), *done[4:]]

        home = service.home()

        assert runner.run.call_count == 4
        assert home.figures is None
        assert home.figures_error is not None
        assert home.figures_error.code == "timeout"
        assert home.status.as_of_date == date(2026, 10, 31)
        assert home.status.build is not None

    def test_no_recorded_build_has_its_note(self, service: HomeService, builds: BuildStatusReader) -> None:
        builds.last_full_build.return_value = None

        status = service.home().status

        assert (status.build, status.build_note) == (None, NO_BUILD_NOTE)

    def test_unreadable_build_has_its_note_and_leaves_the_rest(
        self, service: HomeService, builds: BuildStatusReader
    ) -> None:
        builds.last_full_build.side_effect = BuildStatusUnavailable("down")

        home = service.home()

        assert (home.status.build, home.status.build_note) == (None, BUILD_UNAVAILABLE_NOTE)
        assert home.figures is not None
        assert home.status.freshness == FRESHNESS

    def test_missing_freshness_has_its_note(self, service: HomeService, freshness: FreshnessReader) -> None:
        freshness.read.return_value = None

        status = service.home().status

        assert (status.freshness, status.freshness_note) == (None, NO_FRESHNESS_NOTE)

    def test_unreadable_freshness_has_its_note(self, service: HomeService, freshness: FreshnessReader) -> None:
        freshness.read.side_effect = FreshnessUnavailable("half-written")

        home = service.home()

        assert (home.status.freshness, home.status.freshness_note) == (None, FRESHNESS_UNAVAILABLE_NOTE)
        assert home.status.build is not None
