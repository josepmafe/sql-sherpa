from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import SIMULATION_SEED, TRAFFIC_MONTH_END, make_customer
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.behaviour.visitor_traffic import VisitorTrafficSimulator
from sherpa_generator.records import (
    Campaign,
    CampaignChannel,
    EventType,
    PendingEvent,
    ProductVariant,
    SalesChannel,
    TrackedEvent,
)
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules
from sherpa_generator.simulation.store_snapshot import StoreSnapshot

if TYPE_CHECKING:
    import psycopg
    from sherpa_generator.database.connection import Database

DAY = date(2025, 3, 12)  # a Wednesday: seasonality 1.0
END_OF_RUN = datetime(TRAFFIC_MONTH_END.year, TRAFFIC_MONTH_END.month, TRAFFIC_MONTH_END.day, tzinfo=UTC)
PAID_MEDIUMS = {"cpc", "paid_social", "affiliate"}
EventRow = tuple[str, str, str | None, str, datetime, str | None, str | None]


class TestVisitorTrafficSimulator:
    @pytest.mark.integration
    def test_session_conversion_rate(self, traffic_month: Database) -> None:
        with traffic_month.connect() as conn:
            types = Counter(row[3] for row in _distinct_events(conn))

        rate = types[EventType.PURCHASE] / types[EventType.PAGE_VIEW]

        assert 0.025 <= rate <= 0.045

    @pytest.mark.integration
    def test_paid_search_share(self, traffic_month: Database) -> None:
        with traffic_month.connect() as conn:
            page_views = [row for row in _distinct_events(conn) if row[3] == EventType.PAGE_VIEW]

        share = sum(1 for row in page_views if row[5] == "cpc") / len(page_views)

        assert 0.14 <= share <= 0.22

    @pytest.mark.integration
    def test_no_received_at_beyond_last_simulated_day_and_pending_events_are_future(
        self, traffic_month: Database
    ) -> None:
        with traffic_month.connect() as conn:
            landed_max = _scalar(conn, "SELECT max(received_at) FROM raw_events.events")
            pending_min = _scalar(conn, "SELECT min(received_at) FROM raw_meta.pending_events")
            occurred = [datetime.fromisoformat(row[0]) for row in conn.execute(_ALL_EVENTS_OCCURRED).fetchall()]
            customer_max = _scalar(conn, "SELECT max(created_at) FROM raw_shop.customers")

        assert landed_max < END_OF_RUN
        assert pending_min >= END_OF_RUN
        assert max(occurred) < END_OF_RUN
        assert customer_max < END_OF_RUN

    @pytest.mark.integration
    def test_new_visitor_events_are_anonymous_until_login_or_signup(self, traffic_month: Database) -> None:
        with traffic_month.connect() as conn:
            events = _distinct_events(conn)
            customer_count = _scalar(conn, "SELECT count(*) FROM raw_shop.customers")

        by_device: dict[str, list[EventRow]] = defaultdict(list)
        for row in events:
            by_device[row[1]].append(row)
        signups = [row for row in events if row[3] == EventType.SIGNUP]
        identified = {row[1] for row in events if row[3] in {EventType.SIGNUP, EventType.LOGIN}}

        assert len(signups) == customer_count
        for signup in signups:
            device_events = by_device[signup[1]]
            assert signup[2] is not None
            assert all(row[2] is None for row in device_events if row[4] < signup[4])
            assert {row[2] for row in device_events if row[2] is not None} == {signup[2]}
        assert all(row[2] is None for row in events if row[1] not in identified)

    def test_zero_scale_day_produces_no_sessions(self) -> None:
        changes = DayChanges()

        outcome = _simulator(scale=0.0).simulate_day(_snapshot(), changes)

        assert outcome.purchase_intents == []
        assert outcome.paid_sessions_by_campaign == {}
        assert outcome.new_customers == []
        assert changes.insert_counts() == {}

    def test_day_without_active_paid_campaigns_reassigns_paid_share(self) -> None:
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(campaigns={}), changes)

        page_views = [e for e in _events(changes) if e.event_type == EventType.PAGE_VIEW]
        direct = [e for e in page_views if e.context.utm_medium is None and e.context.referrer is None]
        assert len(page_views) > 300
        assert outcome.paid_sessions_by_campaign == {}
        assert not any(e.context.utm_medium in PAID_MEDIUMS for e in _events(changes))
        assert 0.31 <= len(direct) / len(page_views) <= 0.46  # direct's 0.25 of the remaining 0.65

    def test_paid_sessions_are_counted_per_active_campaign(self) -> None:
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(), changes)

        campaigns = {c.utm_campaign: c.id for cs in _campaigns().values() for c in cs}
        page_views = {e.event_id: e for e in _events(changes) if e.event_type == EventType.PAGE_VIEW}  # without R1
        paid_views = Counter(
            campaigns[e.context.utm_campaign] for e in page_views.values() if e.context.utm_medium in PAID_MEDIUMS
        )
        assert outcome.paid_sessions_by_campaign == dict(paid_views)
        assert set(paid_views) == {c.id for cs in _campaigns().values() for c in cs if c.channel != "email"}

    def test_day_without_customers_has_only_new_visitors_and_no_app_purchases(self) -> None:
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(customers=0), changes)

        events = _events(changes)
        assert outcome.purchase_intents
        assert all(i.sales_channel == SalesChannel.WEB for i in outcome.purchase_intents)
        assert not any(e.event_type == EventType.LOGIN for e in events)
        assert {i.customer_id for i in outcome.purchase_intents} <= {c.id for c in outcome.new_customers}

    def test_app_purchases_complement_web_purchases_without_events(self) -> None:
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(), changes)

        web = [i for i in outcome.purchase_intents if i.sales_channel == SalesChannel.WEB]
        app = [i for i in outcome.purchase_intents if i.sales_channel == SalesChannel.APP]
        purchase_ids = {e.event_id for e in _events(changes) if e.event_type == EventType.PURCHASE}
        start, end = SimulationCalendar().day_bounds(DAY)
        assert web
        assert len(app) == round(len(web) * 0.35 / 0.65)
        assert {i.purchase_event_id for i in web} == purchase_ids
        assert all(i.purchase_event_id is None and i.session_anonymous_id is None for i in app)
        assert {i.customer_id for i in app} <= {c.id for c in _snapshot().customers}
        assert all(start <= i.purchased_at < end for i in outcome.purchase_intents)

    def test_late_events_become_pending_and_never_occur_after_the_day(self) -> None:
        changes = DayChanges()

        _simulator().simulate_day(_snapshot(), changes)

        start, end = SimulationCalendar().day_bounds(DAY)
        landed, pending = changes.inserted(TrackedEvent), changes.inserted(PendingEvent)
        assert pending
        assert all(start <= e.received_at < end for e in landed)
        assert all(e.received_at >= end for e in pending)
        assert all(datetime.fromisoformat(e.occurred_at) < end for e in [*landed, *pending])
        assert len({e.event_id for e in landed}) < len(landed)  # R1 duplicates land with the originals

    def test_session_starts_do_not_pile_up_on_one_second(self) -> None:
        changes = DayChanges()

        _simulator().simulate_day(_snapshot(), changes)

        page_views = {e.event_id: e for e in _events(changes) if e.event_type == EventType.PAGE_VIEW}
        starts = Counter(datetime.fromisoformat(e.occurred_at) for e in page_views.values())
        assert len(page_views) > 300
        assert max(starts.values()) <= 3

    def test_simulate_day_is_deterministic(self) -> None:
        first, second = DayChanges(), DayChanges()

        outcome_a = _simulator().simulate_day(_snapshot(), first)
        outcome_b = _simulator().simulate_day(_snapshot(), second)

        assert outcome_a == outcome_b
        assert _events(first) == _events(second)


_ALL_EVENTS_OCCURRED = (
    "SELECT occurred_at FROM raw_events.events UNION ALL SELECT occurred_at FROM raw_meta.pending_events"
)


def _distinct_events(conn: psycopg.Connection) -> list[EventRow]:
    """Events and pending events, one row per event_id (R1 duplicates removed), ordered by occurrence."""
    rows = conn.execute(
        "SELECT DISTINCT ON (event_id) event_id, anonymous_id, user_id, event_type, occurred_at,"
        " context->>'utm_medium', context->>'utm_campaign'"
        " FROM (SELECT * FROM raw_events.events UNION ALL SELECT * FROM raw_meta.pending_events) e"
        " ORDER BY event_id"
    ).fetchall()
    events = [(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4]), r[5], r[6]) for r in rows]
    return sorted(events, key=lambda row: row[4])


def _scalar(conn: psycopg.Connection, query: str) -> datetime | int:
    row = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return row[0]


def _simulator(scale: float = 1.0) -> VisitorTrafficSimulator:
    return VisitorTrafficSimulator(
        DeterministicRandom(SIMULATION_SEED),
        PrimaryKeyAllocator({}),
        RealismRules(),
        SimulationCalendar(),
        scale,
    )


def _snapshot(customers: int = 50, campaigns: dict[CampaignChannel, list[Campaign]] | None = None) -> StoreSnapshot:
    catalog = DayChanges()
    CatalogBuilder(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({})).build(catalog, DAY, DAY)
    variants = catalog.inserted(ProductVariant)
    people = [make_customer(customer_id) for customer_id in range(1, customers + 1)]
    return StoreSnapshot(
        day=DAY,
        variants=variants,
        variant_popularity={v.id: 1 / len(variants) for v in variants},
        customers=people,
        customer_propensity={c.id: 1.0 + c.id % 5 for c in people},
        customer_devices={c.id: [f"device-{c.id}"] for c in people},
        leads=[],
        active_campaigns=_campaigns() if campaigns is None else campaigns,
        recent_email_sends=[],
        active_promotions=[],
        coupon_codes=[],
        shipping_address_ids={},
        open_orders=[],
        open_shipments=[],
        captured_payments=[],
    )


def _campaigns() -> dict[CampaignChannel, list[Campaign]]:
    start = datetime(2025, 3, 1, tzinfo=UTC)
    channels = (CampaignChannel.PAID_SEARCH, CampaignChannel.PAID_SOCIAL, CampaignChannel.AFFILIATE)
    campaigns: dict[CampaignChannel, list[Campaign]] = {}
    for index, channel in enumerate((*channels, CampaignChannel.EMAIL), start=1):
        campaigns[channel] = [
            Campaign(
                id=index,
                name=f"Campaign {index}",
                utm_campaign=f"campaign-{index}",
                channel=channel,
                starts_at=start,
                ends_at=start + timedelta(days=30),
                budget=1000,
            )
        ]
    return campaigns


def _events(changes: DayChanges) -> list[TrackedEvent]:
    return [*changes.inserted(TrackedEvent), *changes.inserted(PendingEvent)]
