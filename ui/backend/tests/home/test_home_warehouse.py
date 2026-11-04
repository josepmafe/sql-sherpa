from __future__ import annotations

import itertools
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sherpa_ui.build.build_status import BuildStatusReader
from sherpa_ui.home.data_status import FreshnessReader
from sherpa_ui.home.home_queries import HOME_QUERIES_PATH, HomeQueries
from sherpa_ui.home.home_service import Home, HomeService
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
    import psycopg
    from sherpa_ui.readonly.query_runner import QueryRunner
    from sherpa_ui.settings import UiSettings

pytestmark = pytest.mark.warehouse

MAX_TREND_WEEKS = 26  # UI spec §6.2
LATEST_FULL_BUILD_ROWS = """
select count(*) from ops.dbt_run_results where invocation_id = (
    select invocation_id from ops.dbt_run_results
    where target_name = 'prod' and command = 'build' and is_full_run
    group by invocation_id order by max(run_started_at) desc, invocation_id desc limit 1
)
"""


@pytest.fixture(scope="module")
def home(runner: QueryRunner, ui_settings: UiSettings) -> Home:
    service = HomeService(
        runner,
        BuildStatusReader(ui_settings),
        FreshnessReader(REPO_ROOT / ui_settings.ui_dbt_target_path),
        HomeQueries.from_yaml(HOME_QUERIES_PATH),
    )
    return service.home()


class TestHomeWarehouse:
    def test_five_tiles_have_values(self, home: Home) -> None:
        assert home.figures is not None
        assert len(home.figures.tiles) == 5
        assert all(tile.value is not None for tile in home.figures.tiles)

    def test_trend_is_consecutive_mondays_ending_at_the_as_of_week(self, home: Home) -> None:
        assert home.figures is not None
        assert home.status.as_of_date is not None
        weeks = [week.week_start for week in home.figures.trend]
        assert 0 < len(weeks) <= MAX_TREND_WEEKS
        assert all(week.weekday() == 0 for week in weeks)
        assert all(later - earlier == timedelta(days=7) for earlier, later in itertools.pairwise(weeks))
        assert weeks[-1] == home.status.as_of_date - timedelta(days=home.status.as_of_date.weekday())

    def test_as_of_date_is_the_latest_order_date(self, home: Home, admin: psycopg.Connection) -> None:
        row = admin.execute("select max(order_date) from marts.fct_orders").fetchone()

        assert row is not None
        assert home.status.as_of_date == row[0]

    def test_build_node_count_is_the_latest_full_prod_build(self, home: Home, admin: psycopg.Connection) -> None:
        row = admin.execute(LATEST_FULL_BUILD_ROWS).fetchone()

        assert home.status.build is not None
        assert row is not None
        assert home.status.build.node_count == row[0]

    def test_black_friday_flags_match_dim_date(self, home: Home, admin: psycopg.Connection) -> None:
        assert home.figures is not None
        flags = dict(admin.execute("select date_day, is_black_friday_week from marts.dim_date").fetchall())

        assert {w.week_start: w.is_black_friday_week for w in home.figures.trend} == {
            w.week_start: flags[w.week_start] for w in home.figures.trend
        }
