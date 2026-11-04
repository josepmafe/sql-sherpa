from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal
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

ROUNDING = Decimal("1e-9")  # Postgres divides to 16 decimals
MAX_TREND_WEEKS = 26  # UI spec §6.2
LATEST_FULL_BUILD_ROWS = """
select count(*) from ops.dbt_run_results where invocation_id = (
    select invocation_id from ops.dbt_run_results
    where target_name = 'prod' and command = 'build' and is_full_run
    group by invocation_id order by max(run_started_at) desc, invocation_id desc limit 1
)
"""


# Per earlier-window order: gross - discount minus the refunds paid by as_of - 30, written without the Home query's
# shape; and the later refunds, so the two together can be reconciled with fct_orders.net_revenue.
EARLIER_WINDOW = """
with as_of as (select max(order_date) as day from marts.fct_orders),
earlier as (
    select o.order_id, o.gross_revenue - o.discount_amount as billed, o.net_revenue
    from marts.fct_orders o, as_of
    where not o.is_cancelled and o.order_date > as_of.day - 60 and o.order_date <= as_of.day - 30
)
select
    count(*),
    sum(e.billed - (select coalesce(sum(r.revenue_amount), 0) from marts.fct_refunds r, as_of
                    where r.order_id = e.order_id and r.refund_date <= as_of.day - 30)),
    sum((select coalesce(sum(r.revenue_amount), 0) from marts.fct_refunds r, as_of
         where r.order_id = e.order_id and r.refund_date > as_of.day - 30)),
    sum(e.net_revenue)
from earlier e
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


class TestEarlierWindowAsItStood:
    def test_the_previous_period_has_only_the_refunds_known_30_days_ago(
        self, home: Home, admin: psycopg.Connection
    ) -> None:
        row = admin.execute(EARLIER_WINDOW).fetchone()
        assert home.figures is not None
        tiles = {tile.id: tile for tile in home.figures.tiles}
        assert row is not None
        _, as_stood, later_refunds, net_now = row

        assert as_stood - later_refunds == net_now  # the refunds that came later bring it to fct_orders.net_revenue
        assert later_refunds > 0  # the window would not tell the rule from the plain net revenue otherwise
        this_net = tiles["net_revenue"].value
        assert this_net is not None
        change = tiles["net_revenue"].change
        assert change is not None
        assert change == pytest.approx((this_net - as_stood) / as_stood, abs=ROUNDING)

    def test_aov_compares_with_the_as_stood_net_revenue_per_order(self, home: Home, admin: psycopg.Connection) -> None:
        row = admin.execute(EARLIER_WINDOW).fetchone()
        assert home.figures is not None
        aov = next(tile for tile in home.figures.tiles if tile.id == "aov")
        assert row is not None
        orders, as_stood, _, _ = row

        assert aov.value is not None
        assert aov.change is not None
        assert aov.change == pytest.approx((aov.value - as_stood / orders) / (as_stood / orders), abs=ROUNDING)
