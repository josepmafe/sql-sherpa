from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from helpers import MONTH_END, assert_rate

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

pytestmark = pytest.mark.integration

# Each row is a rule, the SQL counting (hits, trials) on the raw tables of the shared `month` run, and the spec rate.
# The rules whose month sample is too small for assert_rate (R3: ~70 customers and ~100 leads; R4 to R9 and the 3 %
# click rate) are measured on larger runs next to their behaviour: test_visitor_traffic, test_crm_activity,
# test_order_lifecycle and test_after_sales.
EVENTS = """
    SELECT event_id, min(received_at) AS received_at, min(occurred_at::timestamptz) AS occurred_at
    FROM (SELECT * FROM raw_events.events UNION ALL SELECT * FROM raw_meta.pending_events) e
    GROUP BY event_id
"""
OPEN_WINDOW_DAYS = 3  # sends newer than this may still be opened (the open delay is up to 48 h)

RATE_ROWS = [
    # R1: every duplicate is a second row of an event_id that landed
    pytest.param(
        "SELECT count(*) - count(DISTINCT event_id), count(DISTINCT event_id) FROM raw_events.events",
        0.01,  # Spec A §4.4 R1: 1 % of events
        id="R1-duplicated-events",
    ),
    # R2: an on-time event arrives within seconds; a late one 1-3 days after it occurred
    pytest.param(
        f"SELECT count(*) FILTER (WHERE received_at - occurred_at >= interval '1 day'), count(*) FROM ({EVENTS}) e",
        0.02,  # Spec A §4.4 R2: 2 % of events
        id="R2-late-events",
    ),
    pytest.param(
        "SELECT count(*) FILTER (WHERE opened_at IS NOT NULL), count(*) FROM raw_crm.email_sends"
        f" WHERE sent_at < '{MONTH_END}'::date - {OPEN_WINDOW_DAYS}",
        0.25,  # Spec A §5.2 step 2: open 25 % of sends
        id="email-opens",
    ),
]


@pytest.mark.parametrize(("sql", "expected"), RATE_ROWS)
def test_rule_rate_on_the_month(month: Database, sql: str, expected: float) -> None:
    with month.connect() as conn:
        row = conn.execute(sql).fetchone()  # type: ignore[call-overload]  # static SQL only

    assert row is not None
    hits, trials = row
    assert_rate(hits, trials, expected)


def test_a_customer_has_one_to_three_devices(month: Database) -> None:
    with month.connect() as conn:
        row = conn.execute(
            "SELECT min(devices), max(devices) FROM (SELECT count(DISTINCT anonymous_id) AS devices FROM"
            " (SELECT user_id, anonymous_id FROM raw_events.events UNION ALL"
            "  SELECT user_id, anonymous_id FROM raw_meta.pending_events) e"
            " WHERE user_id IS NOT NULL GROUP BY user_id) d"
        ).fetchone()

    assert row is not None
    assert row[1] <= 3  # Spec A §5.2 step 3: each customer has 1-3 devices
