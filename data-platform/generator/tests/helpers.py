from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.records import Customer, Order, OrderStatus, RawRecord, SalesChannel
from sherpa_generator.simulation.simulation_state import SimulationStateStore
from sherpa_generator.simulation.store_snapshot import StoreSnapshot

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)
STATE_TABLE = "raw_meta.simulation_state"

# The shared statistical run (conftest `traffic_month`): Spec A §5.4, a 30-day run at scale 0.2.
SIMULATION_SEED = 42
TRAFFIC_MONTH_DAYS = 30
TRAFFIC_MONTH_SCALE = 0.2
TRAFFIC_MONTH_START = date(2025, 3, 1)
TRAFFIC_MONTH_END = date(2025, 3, 31)  # exclusive: the day after the last simulated day
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


def make_customer(customer_id: int) -> Customer:
    return Customer(
        id=customer_id,
        email=f"c{customer_id}@example.com",
        first_name="A",
        last_name="B",
        country_code="DE",
        marketing_opt_in=True,
        is_test_account=False,
        created_at=NOW,
        updated_at=NOW,
    )


def make_order(order_id: int) -> Order:
    return Order(
        id=order_id,
        order_number=f"SS-{order_id:06d}",
        customer_id=1,
        shipping_address_id=1,
        status=OrderStatus.PLACED,
        subtotal=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        shipping_amount=Decimal("4.95"),
        tax_amount=Decimal("2.00"),
        order_total=Decimal("16.95"),
        sales_channel=SalesChannel.WEB,
        placed_at=NOW,
        updated_at=NOW,
    )


def make_snapshot(day: date, **fields: object) -> StoreSnapshot:
    """An empty shop on `day`; a test passes only the parts it needs."""
    return StoreSnapshot.model_validate({**_EMPTY_SNAPSHOT, "day": day, **fields})


def _subclasses(cls: type[RawRecord]) -> list[type[RawRecord]]:
    found: list[type[RawRecord]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found
