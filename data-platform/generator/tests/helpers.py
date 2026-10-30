from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from math import sqrt
from typing import TYPE_CHECKING, Any

from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.records import (
    Campaign,
    CampaignChannel,
    Customer,
    DeviceType,
    EventContext,
    EventProperties,
    EventType,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    ProductVariant,
    Promotion,
    RawRecord,
    SalesChannel,
    TrackedEvent,
)
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.simulation_state import SimulationState, SimulationStateStore
from sherpa_generator.simulation.store_snapshot import StoreSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import psycopg
    from sherpa_generator.database.connection import Database

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)
STATE_TABLE = "raw_meta.simulation_state"

SIMULATION_SEED = 42
# The shared statistical run (conftest `month`): Spec A §5.4, a 30-day Simulator run at scale 0.2 with RUN_SEED.
MONTH_DAYS = 30
MONTH_SCALE = 0.2
MONTH_START = date(2025, 3, 1)
MONTH_END = date(2025, 3, 31)  # exclusive: the day after the last simulated day, and the run's `today`
MONTH_END_AT = datetime(MONTH_END.year, MONTH_END.month, MONTH_END.day, tzinfo=UTC)
# The shared after-sales run (conftest `after_sales_season`): the smallest that yields returns in every status and
# goodwill refunds (~285 orders, ~18 returns).
AFTER_SALES_DAYS = 45
AFTER_SALES_SCALE = 0.3
AFTER_SALES_START = date(2025, 3, 1)
# The Simulator's reference run (conftest `simulator_reference`): 10 days, seed 7, scale 0.1, from 2025-03-01.
RUN_SEED = 7
RUN_SCALE = 0.1
RUN_DAYS = 10
RUN_START = date(2025, 3, 1)
RUN_TODAY = date(2025, 4, 1)
# The full-year run (conftest `full_year`): 365 days ending yesterday, seed 42, scale 1.0.
FULL_YEAR_SEED = 42
FULL_YEAR_SCALE = 1.0
FULL_YEAR_DAYS = 365
FULL_YEAR_TODAY = date(2026, 1, 1)
_EMPTY_SNAPSHOT: dict[str, object] = {
    "variants": [],
    "variant_popularity": {},
    "products": [],
    "product_category_l1": {},
    "customers": [],
    "customer_propensity": {},
    "customer_devices": {},
    "leads": [],
    "active_campaigns": {},
    "recent_email_sends": [],
    "active_promotions": [],
    "coupon_codes": [],
    "shipping_address_ids": {},
    "open_orders": [],
    "open_shipments": [],
    "captured_payments": [],
    "recently_delivered_orders": [],
    "recently_delivered_items": [],
    "recently_delivered_payments": [],
    "open_returns": [],
}


def all_record_types() -> list[type[RawRecord]]:
    return _subclasses(RawRecord)


def fingerprint(db: Database) -> dict[str, object]:
    """Per raw table, the row count and an md5 of every row in a total order, plus the resumable simulation state.

    Hashing whole rows (not just keys) also catches updates that a resumed run applies differently.
    """
    tables = [table for table in RawSchema(db).tables() if table != STATE_TABLE]
    with db.connect() as conn:
        result: dict[str, object] = {
            table: conn.execute(
                f"SELECT count(*), md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {table} t"
            ).fetchone()
            for table in tables
        }
        result[STATE_TABLE] = SimulationStateStore(conn).load()
    return result


def assert_rate(hits: int, trials: int, expected: float, *, z: float = 4.0) -> None:
    """Fail when hits / trials lies outside `expected` +/- z binomial standard deviations.

    A sample whose bound includes 0 would pass a rule that does nothing, so it is refused instead of measured. A bound
    that excludes 0 stays below twice `expected`, so it also excludes a rule that fires ten times too often.
    """
    half = z * sqrt(expected * (1 - expected) / trials) if trials > 0 else float("inf")
    low, high = expected - half, expected + half
    if low <= 0:
        msg = f"sample too small: {trials} trials give {expected:.4f} +/- {half:.4f}, which cannot exclude 0"
        raise AssertionError(msg)

    if not low <= hits / trials <= high:
        msg = (
            f"rate {hits / trials:.4f} ({hits}/{trials}) outside {expected:.4f} +/- {half:.4f} "
            f"= [{low:.4f}, {high:.4f}] (z={z})"
        )
        raise AssertionError(msg)


def make_customer(customer_id: int, **overrides: object) -> Customer:
    """An opted-in German customer who signed up at NOW; a test states the fields it relies on."""
    fields: dict[str, object] = {
        "id": customer_id,
        "email": f"c{customer_id}@example.com",
        "first_name": "A",
        "last_name": "B",
        "country_code": "DE",
        "marketing_opt_in": True,
        "is_test_account": False,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return Customer(**{**fields, **overrides})


def make_order(order_id: int, **overrides: object) -> Order:
    """A placed web order of 16.95 EUR from customer 1 at NOW; a test states the fields it relies on."""
    fields: dict[str, object] = {
        "id": order_id,
        "order_number": f"SS-{order_id:06d}",
        "customer_id": 1,
        "shipping_address_id": 1,
        "status": OrderStatus.PLACED,
        "subtotal": Decimal("10.00"),
        "discount_total": Decimal("0.00"),
        "shipping_amount": Decimal("4.95"),
        "tax_amount": Decimal("2.00"),
        "order_total": Decimal("16.95"),
        "sales_channel": SalesChannel.WEB,
        "placed_at": NOW,
        "updated_at": NOW,
    }
    return Order(**{**fields, **overrides})


def make_payment(payment_id: int, **overrides: object) -> Payment:
    """A captured 10 EUR card payment on order 1 at NOW."""
    fields: dict[str, object] = {
        "id": payment_id,
        "order_id": 1,
        "method": PaymentMethod.CARD,
        "amount": Decimal("10.00"),
        "status": PaymentStatus.CAPTURED,
        "created_at": NOW,
    }
    return Payment(**{**fields, **overrides})


def make_item(item_id: int, **overrides: object) -> OrderItem:
    """One unit of variant 1 at 10 EUR on order 1."""
    fields: dict[str, object] = {
        "id": item_id,
        "order_id": 1,
        "variant_id": 1,
        "quantity": 1,
        "unit_price": Decimal("10.00"),
    }
    return OrderItem(**{**fields, **overrides})


def make_event(event_id: str, cls: type[TrackedEvent] = TrackedEvent, **overrides: object) -> TrackedEvent:
    """A purchase of order 7 from a mobile device, received at NOW."""
    fields: dict[str, object] = {
        "event_id": event_id,
        "anonymous_id": "anon-1",
        "event_type": EventType.PURCHASE,
        "occurred_at": "2025-01-01T13:00:00+01:00",
        "received_at": NOW,
        "context": EventContext(device_type=DeviceType.MOBILE, utm_source="google"),
        "properties": EventProperties(order_id=7, cart_value=Decimal("20.00")),
    }
    return cls(**{**fields, **overrides})


def make_campaign(campaign_id: int, **overrides: object) -> Campaign:
    """A 1,000 EUR paid-search campaign that starts on 2025-03-01 and runs 30 days."""
    starts_at = datetime(2025, 3, 1, tzinfo=UTC)
    fields: dict[str, object] = {
        "id": campaign_id,
        "name": f"Campaign {campaign_id}",
        "utm_campaign": f"campaign-{campaign_id}",
        "channel": CampaignChannel.PAID_SEARCH,
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(days=30),
        "budget": Decimal("1000"),
    }
    return Campaign(**{**fields, **overrides})


def make_snapshot(day: date, **fields: object) -> StoreSnapshot:
    """An empty shop on `day`; a test passes only the parts it needs."""
    return StoreSnapshot.model_validate({**_EMPTY_SNAPSHOT, "day": day, **fields})


@dataclass(frozen=True)
class Catalog:
    variants: list[ProductVariant]
    promotions: list[Promotion]


@cache
def catalog_variants(day: date) -> Catalog:
    """What the CatalogBuilder creates on `day` with SIMULATION_SEED, built once per day. Do not mutate it."""
    changes = DayChanges()
    CatalogBuilder(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({})).build(changes, day, day)
    return Catalog(changes.inserted(ProductVariant), changes.inserted(Promotion))


def shop_snapshot(day: date, *, customers: int | Sequence[Customer] = 50, **fields: object) -> StoreSnapshot:
    """The catalog of `day` and `customers` (a count, or the customers themselves) with propensity 1.0 and a
    shipping address each, nothing else; a test overrides the parts it needs."""
    people = (
        [make_customer(customer_id) for customer_id in range(1, customers + 1)]
        if isinstance(customers, int)
        else list(customers)
    )
    variants = catalog_variants(day).variants
    return make_snapshot(
        day,
        **{
            "variants": variants,
            "variant_popularity": {v.id: 1 / len(variants) for v in variants},
            "customers": people,
            "customer_propensity": {c.id: 1.0 for c in people},
            "shipping_address_ids": {c.id: 1000 + c.id for c in people},
            **fields,
        },
    )


def run_days(
    simulate_day: Callable[[StoreSnapshot, DayChanges], None], snapshot: StoreSnapshot, first: date, days: int
) -> list[tuple[date, DayChanges]]:
    """The same shop on `days` consecutive days, each with fresh changes (the shop itself does not move on)."""
    result: list[tuple[date, DayChanges]] = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        changes = DayChanges()
        simulate_day(snapshot.model_copy(update={"day": day}), changes)
        result.append((day, changes))
    return result


def fetch_all(conn: psycopg.Connection, query: str) -> list[tuple[Any, ...]]:
    return conn.execute(query).fetchall()  # type: ignore[arg-type]  # static SQL only


def scalar(conn: psycopg.Connection, query: str) -> Any:
    row = conn.execute(query).fetchone()  # type: ignore[arg-type]  # static SQL only
    assert row is not None
    return row[0]


def stored_state(db: Database) -> SimulationState:
    with db.connect() as conn:
        state = SimulationStateStore(conn).load()

    assert state is not None
    return state


def assert_empty(db: Database) -> None:
    tables = fingerprint(db)
    assert tables.pop(STATE_TABLE) is None
    assert all(count == 0 for count, _ in tables.values())


def _subclasses(cls: type[RawRecord]) -> list[type[RawRecord]]:
    found: list[type[RawRecord]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found
