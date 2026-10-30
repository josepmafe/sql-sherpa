from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import MONTH_END, SIMULATION_SEED, assert_rate, make_customer
from sherpa_generator.behaviour import visitor_traffic
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.behaviour.visitor_traffic import (
    EVENT_GAP_SECONDS,
    MAX_SESSION_GAPS,
    TrafficOutcome,
    VisitorTrafficSimulator,
)
from sherpa_generator.records import (
    Address,
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
END_OF_RUN = datetime(MONTH_END.year, MONTH_END.month, MONTH_END.day, tzinfo=UTC)
PAID_MEDIUMS = {"cpc", "paid_social", "affiliate"}
MAX_SESSION = timedelta(seconds=EVENT_GAP_SECONDS[1] * MAX_SESSION_GAPS)  # the longest a session runs
PURCHASE_RATE_SCALE = 20.0  # one day with enough signups and returning checkouts to measure both purchase rates
SIGNUP_SCALE = 10.0  # one day of 4,000 sessions, each of which signs up (see `signup_day`)
EventRow = tuple[str, str, str | None, str, datetime, str | None, str | None]


class TestVisitorTrafficSimulator:
    @pytest.mark.integration
    def test_session_conversion_rate(self, month: Database) -> None:
        with month.connect() as conn:
            types = Counter(row[3] for row in _distinct_events(conn))

        rate = types[EventType.PURCHASE] / types[EventType.PAGE_VIEW]

        # Spec §5.2: ~3.1%, an approximate target. ~2,500 page views give sd ~0.0035, so z = 4 is 0.017..0.045; the
        # first month has few returning customers, and seed 7 measures 0.0239 (seeds 42/1/2 measure 0.031-0.034).
        assert 0.017 <= rate <= 0.045

    @pytest.mark.integration
    def test_paid_search_share(self, month: Database) -> None:
        with month.connect() as conn:
            page_views = [row for row in _distinct_events(conn) if row[3] == EventType.PAGE_VIEW]

        share = sum(1 for row in page_views if row[5] == "cpc") / len(page_views)

        assert 0.14 <= share <= 0.22

    @pytest.mark.integration
    def test_no_received_at_beyond_last_simulated_day_and_pending_events_are_future(self, month: Database) -> None:
        with month.connect() as conn:
            landed_max = _scalar(conn, "SELECT max(received_at) FROM raw_events.events")
            pending_min = _scalar(conn, "SELECT min(received_at) FROM raw_meta.pending_events")
            occurred = [datetime.fromisoformat(row[0]) for row in conn.execute(_ALL_EVENTS_OCCURRED).fetchall()]
            customer_max = _scalar(conn, "SELECT max(created_at) FROM raw_shop.customers")

        assert landed_max < END_OF_RUN
        assert pending_min >= END_OF_RUN
        assert max(occurred) < END_OF_RUN
        assert customer_max < END_OF_RUN

    @pytest.mark.integration
    def test_new_visitor_events_are_anonymous_until_login_or_signup(self, month: Database) -> None:
        with month.connect() as conn:
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

    def test_email_noise_hits_ten_percent_of_new_customers_and_normalizes_back(self, signup_day: _SignupDay) -> None:
        emails = [c.email for c in signup_day.outcome.new_customers]

        noisy = [email for email in emails if email != email.strip().lower()]

        assert_rate(len(noisy), len(emails), 0.10)  # R3, Spec A §4.4
        assert all(email.strip().lower().count("@") == 1 for email in noisy)

    def test_country_noise_hits_three_percent_of_customers_and_addresses(self, signup_day: _SignupDay) -> None:
        codes = [c.country_code for c in signup_day.outcome.new_customers]
        codes += [a.country_code for a in signup_day.changes.inserted(Address)]

        noisy = [code for code in codes if code not in RealismRules.COUNTRY_WEIGHTS]

        assert_rate(len(noisy), len(codes), 0.03)  # R4, Spec A §4.4: 3 % of customers/addresses
        assert {code.upper().replace("UK", "GB") for code in noisy} <= set(RealismRules.COUNTRY_WEIGHTS)

    def test_test_accounts_hit_half_a_percent_of_customers_use_the_test_domain_and_place_orders(
        self, signup_day: _SignupDay
    ) -> None:
        customers = signup_day.outcome.new_customers
        test_accounts = [c for c in customers if c.is_test_account]
        domain = f"@{RealismRules.TEST_EMAIL_DOMAIN}"

        assert_rate(len(test_accounts), len(customers), 0.005)  # R5, Spec A §4.4: 0.5 % of customers
        assert all(c.email.strip().lower().endswith(domain) for c in test_accounts)
        assert not any(domain in c.email.lower() for c in customers if not c.is_test_account)
        buyers = {i.customer_id for i in signup_day.outcome.purchase_intents}
        assert buyers & {c.id for c in test_accounts}  # they "place orders" (R5)

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

    def test_new_visitors_buy_less_often_after_signup_than_returning_customers_at_checkout(self) -> None:
        changes = DayChanges()

        _simulator(scale=PURCHASE_RATE_SCALE).simulate_day(_snapshot(), changes)

        events = list({e.event_id: e for e in _events(changes)}.values())  # without R1
        signed_up = {e.anonymous_id for e in events if e.event_type == EventType.SIGNUP}
        returning = {e.anonymous_id for e in events if e.event_type == EventType.LOGIN}
        purchased = {e.anonymous_id for e in events if e.event_type == EventType.PURCHASE}
        returning_checkouts = [
            e for e in events if e.event_type == EventType.BEGIN_CHECKOUT and e.anonymous_id in returning
        ]
        returning_purchases = [e for e in events if e.event_type == EventType.PURCHASE and e.anonymous_id in returning]
        assert len(signed_up) > 200
        assert 0.15 <= len(signed_up & purchased) / len(signed_up) <= 0.25  # NEW_VISITOR_PURCHASE_RATE 0.20
        assert len(returning_checkouts) > 250
        assert 0.65 <= len(returning_purchases) / len(returning_checkouts) <= 0.75  # PURCHASE_RATE 0.70

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

    def test_web_purchase_intent_carries_the_variant_its_session_carted(self) -> None:
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(), changes)

        events = list({e.event_id: e for e in _events(changes)}.values())  # without R1
        purchases = {e.event_id: e for e in events if e.event_type == EventType.PURCHASE}
        web = [i for i in outcome.purchase_intents if i.sales_channel == SalesChannel.WEB]
        assert web
        for intent in web:
            purchased_at = datetime.fromisoformat(purchases[str(intent.purchase_event_id)].occurred_at)
            carted = {
                e.properties.variant_id
                for e in events
                if e.event_type == EventType.ADD_TO_CART
                and e.anonymous_id == intent.session_anonymous_id
                and timedelta(0) <= purchased_at - datetime.fromisoformat(e.occurred_at) <= MAX_SESSION
            }
            assert intent.variant_id in carted
        assert all(i.variant_id is None for i in outcome.purchase_intents if i.sales_channel == SalesChannel.APP)

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


@dataclass(frozen=True)
class _SignupDay:
    outcome: TrafficOutcome
    changes: DayChanges


@pytest.fixture(scope="module")
def signup_day() -> _SignupDay:
    """One day on which every session is a new visitor who signs up (about 4,000 customers and 8,000 addresses).

    The rules measured here (R3, R4, R5) act at signup, not in the funnel. A real day signs up one session in 33,
    so the funnel and the signup cap are opened to get enough customers for `assert_rate` in about 3 s. Read-only.
    """
    changes = DayChanges()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(visitor_traffic, "PRODUCT_VIEW_RATE", 1.0)
        patch.setattr(visitor_traffic, "ADD_TO_CART_RATE", 1.0)
        patch.setattr(visitor_traffic, "BEGIN_CHECKOUT_RATE", 1.0)
        patch.setattr(visitor_traffic, "BASE_DAILY_SIGNUPS", visitor_traffic.BASE_DAILY_SESSIONS * 2)
        outcome = _simulator(scale=SIGNUP_SCALE).simulate_day(_snapshot(customers=0), changes)

    return _SignupDay(outcome, changes)


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
        products=[],
        product_category_l1={},
        recently_delivered_orders=[],
        recently_delivered_items=[],
        recently_delivered_payments=[],
        open_returns=[],
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
