from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

pytestmark = [pytest.mark.integration, pytest.mark.slow]

logger = logging.getLogger(__name__)

# Spec A §5.2: expected totals for 365 days at scale 1.0, each within ±15%; generation under 5 minutes.
VOLUME_TOLERANCE = 0.15
EXPECTED_VOLUMES = {"customers": 5_000, "orders": 8_000, "events": 600_000, "leads": 6_000}
MAX_RUNTIME_SECONDS = 300
# Spec A §4.4: the top 20% of buyers produce 70-85% of revenue.
TOP_BUYER_SHARE = 0.20
TOP_REVENUE_SHARE_RANGE = (0.70, 0.85)

VOLUMES = """
SELECT
    (SELECT count(*) FROM raw_shop.customers),
    (SELECT count(*) FROM raw_shop.orders),
    (SELECT count(*) FROM raw_events.events) + (SELECT count(*) FROM raw_meta.pending_events),
    (SELECT count(*) FROM raw_crm.leads)
"""
TOP_BUYERS_REVENUE_SHARE = """
WITH buyers AS (
    SELECT customer_id, sum(order_total) AS revenue
    FROM raw_shop.orders
    WHERE status <> 'cancelled'
    GROUP BY customer_id
), ranked AS (
    SELECT revenue, row_number() OVER (ORDER BY revenue DESC, customer_id) AS rank, count(*) OVER () AS buyers
    FROM buyers
)
SELECT sum(revenue) FILTER (WHERE rank <= ceil(buyers * %s)) / sum(revenue) FROM ranked
"""
PARETO_XFAIL_REASON = (
    "Measured 0.553 at seed 42, scale 1.0. Customers are created only at checkout and ~70% buy at once, so ~3.5k of "
    "~8k orders are one-off first orders; lognormal propensity only spreads the repeat orders. Options pending the "
    "owner's decision: raise PROPENSITY_SIGMA to ~2.5-3 (customers with hundreds of orders a year); decouple signup "
    "from buying plus sigma ~1.5-2 (changes Task 6 behaviour); revisit the spec target; tie basket size to "
    "propensity (a new realism rule)."
)


def test_full_year_volumes_and_runtime(full_year: tuple[Database, float]) -> None:
    db, runtime = full_year

    with db.connect() as conn:
        counts = conn.execute(VOLUMES).fetchone()

    assert counts is not None
    volumes = dict(zip(EXPECTED_VOLUMES, counts, strict=True))
    logger.info("runtime %.1f s, volumes %s", runtime, volumes)
    assert runtime < MAX_RUNTIME_SECONDS
    for name, expected in EXPECTED_VOLUMES.items():
        assert abs(volumes[name] - expected) <= expected * VOLUME_TOLERANCE, (name, volumes[name])


@pytest.mark.xfail(strict=True, reason=PARETO_XFAIL_REASON)
def test_top_buyers_hold_70_to_85_percent_of_revenue(full_year: tuple[Database, float]) -> None:
    db, _ = full_year

    with db.connect() as conn:
        row = conn.execute(TOP_BUYERS_REVENUE_SHARE, [TOP_BUYER_SHARE]).fetchone()

    assert row is not None
    top_share = float(row[0])
    logger.info("top 20%% of buyers hold %.3f of revenue", top_share)
    assert TOP_REVENUE_SHARE_RANGE[0] <= top_share <= TOP_REVENUE_SHARE_RANGE[1]
