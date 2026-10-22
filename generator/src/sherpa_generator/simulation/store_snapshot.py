from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import NormalDist
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from sherpa_generator.records import (
    Address,
    Campaign,
    CampaignChannel,
    CouponCode,
    Customer,
    EmailSend,
    Lead,
    Order,
    Payment,
    ProductVariant,
    Promotion,
    RawRecord,
    Shipment,
)
from sherpa_generator.simulation.calendar import SimulationCalendar

if TYPE_CHECKING:
    import psycopg

    from sherpa_generator.simulation.deterministic_random import DeterministicRandom

PROPENSITY_MU: Final = 0.0  # Spec A §4.4: lognormal customer propensity (Pareto customer value)
PROPENSITY_SIGMA: Final = 1.1  # Spec A §4.4
POPULARITY_ZIPF_EXPONENT: Final = 1.0  # product popularity falls off as 1 / rank
RECENT_EMAIL_SEND_DAYS: Final = 3  # covers the 48 h open window plus the click delay
ACTIVE_PROMOTION: Final = "starts_at < %s AND ends_at > %s"  # overlaps the day; params: day end, day start
# entity_fraction is a multiple of 2**-53 in [0, 1); shifting by half a step keeps it strictly inside (0, 1).
_HALF_FRACTION_STEP: Final = 2.0**-54
_PROPENSITY_DISTRIBUTION: Final = NormalDist(PROPENSITY_MU, PROPENSITY_SIGMA)


def lognormal_propensity(fraction: float) -> float:
    """Inverse-CDF draw of lognormal(mu, sigma) from a fraction in [0, 1)."""
    return math.exp(_PROPENSITY_DISTRIBUTION.inv_cdf(fraction + _HALF_FRACTION_STEP))


class StoreSnapshot(BaseModel):
    """The shop as it stands at the start of one simulated day. Every list is ordered by key."""

    model_config = ConfigDict(frozen=True)

    day: date
    variants: list[ProductVariant]
    variant_popularity: dict[int, float]  # variant id -> share of product views; active products only, sums to 1
    customers: list[Customer]  # not soft-deleted
    customer_propensity: dict[int, float]
    customer_devices: dict[int, list[str]]  # customer id -> anonymous ids seen with that user_id
    leads: list[Lead]
    active_campaigns: dict[CampaignChannel, list[Campaign]]
    recent_email_sends: list[EmailSend]  # sent in the last days and not yet fully engaged
    active_promotions: list[Promotion]  # running at some point of the day
    coupon_codes: list[CouponCode]  # of the active promotions
    shipping_address_ids: dict[int, int]  # customer id -> default shipping address id
    open_orders: list[Order]  # placed, paid or shipped
    open_shipments: list[Shipment]  # not delivered yet
    captured_payments: list[Payment]  # of paid orders, which a cancellation refunds

    def propensity(self, customer_id: int) -> float:
        return self.customer_propensity[customer_id]


class StoreSnapshotLoader:
    """Reads the snapshot from the database, so a resumed run sees exactly what one long run would."""

    def __init__(self, conn: psycopg.Connection, random: DeterministicRandom) -> None:
        self._conn = conn
        self._random = random
        self._calendar = SimulationCalendar()

    def load(self, day: date) -> StoreSnapshot:
        start, end = self._calendar.day_bounds(day)
        variants = self._records(ProductVariant)
        customers = self._records(Customer, "deleted_at IS NULL")
        customer_ids = {c.id for c in customers}
        return StoreSnapshot(
            day=day,
            variants=variants,
            variant_popularity=self._variant_popularity(variants),
            customers=customers,
            customer_propensity={
                c.id: lognormal_propensity(self._random.entity_fraction("customer", c.id, "propensity"))
                for c in customers
            },
            customer_devices={
                customer_id: devices
                for customer_id, devices in self._customer_devices().items()
                if customer_id in customer_ids
            },
            leads=self._records(Lead),
            active_campaigns=self._active_campaigns(start, end),
            recent_email_sends=self._records(
                EmailSend,
                "sent_at >= %s AND sent_at < %s AND (opened_at IS NULL OR clicked_at IS NULL)",
                start - timedelta(days=RECENT_EMAIL_SEND_DAYS),
                start,
            ),
            active_promotions=self._records(Promotion, ACTIVE_PROMOTION, end, start),
            coupon_codes=self._records(
                CouponCode, f"promotion_id IN (SELECT id FROM {Promotion.TABLE} WHERE {ACTIVE_PROMOTION})", end, start
            ),
            shipping_address_ids=self._shipping_address_ids(),
            open_orders=self._records(Order, "status IN ('placed', 'paid', 'shipped')"),
            open_shipments=self._records(Shipment, "delivered_at IS NULL"),
            captured_payments=self._records(
                Payment, f"status = 'captured' AND order_id IN (SELECT id FROM {Order.TABLE} WHERE status = 'paid')"
            ),
        )

    def _records[T: RawRecord](self, record_type: type[T], where: str = "TRUE", *params: object) -> list[T]:
        columns = record_type.columns()
        query = (
            f"SELECT {', '.join(columns)} FROM {record_type.TABLE} WHERE {where} ORDER BY {', '.join(record_type.KEY)}"
        )
        rows = self._conn.execute(query, list(params)).fetchall()  # type: ignore[arg-type]  # static SQL only
        return [record_type.model_validate(dict(zip(columns, row, strict=True))) for row in rows]

    def _variant_popularity(self, variants: list[ProductVariant]) -> dict[int, float]:
        """Zipf over all products ranked by a fixed per-product fraction, split evenly over each product's variants."""
        products = self._conn.execute("SELECT id, is_active FROM raw_shop.products").fetchall()
        ranked = sorted(products, key=lambda p: self._random.entity_fraction("product", p[0], "popularity"))
        active_weight = {
            product_id: 1 / rank**POPULARITY_ZIPF_EXPONENT
            for rank, (product_id, is_active) in enumerate(ranked, start=1)
            if is_active
        }
        variants_per_product: dict[int, int] = {}
        for variant in variants:
            variants_per_product[variant.product_id] = variants_per_product.get(variant.product_id, 0) + 1

        weights = {
            v.id: active_weight[v.product_id] / variants_per_product[v.product_id]
            for v in variants
            if v.product_id in active_weight
        }
        total = sum(weights.values())
        return {variant_id: weight / total for variant_id, weight in weights.items()}

    def _shipping_address_ids(self) -> dict[int, int]:
        rows = self._conn.execute(
            f"SELECT customer_id, id FROM {Address.TABLE} WHERE type = 'shipping' AND is_default ORDER BY customer_id"  # type: ignore[arg-type]  # static SQL only
        ).fetchall()
        return dict(rows)

    def _customer_devices(self) -> dict[int, list[str]]:
        rows = self._conn.execute(
            "SELECT user_id::bigint, anonymous_id FROM raw_events.events WHERE user_id IS NOT NULL"
            " UNION SELECT user_id::bigint, anonymous_id FROM raw_meta.pending_events WHERE user_id IS NOT NULL"
            " ORDER BY 1, 2"
        ).fetchall()
        devices: dict[int, list[str]] = {}
        for customer_id, anonymous_id in rows:
            devices.setdefault(customer_id, []).append(anonymous_id)
        return devices

    def _active_campaigns(self, start: datetime, end: datetime) -> dict[CampaignChannel, list[Campaign]]:
        active: dict[CampaignChannel, list[Campaign]] = {}
        for campaign in self._records(Campaign, "starts_at < %s AND ends_at > %s", end, start):
            active.setdefault(campaign.channel, []).append(campaign)
        return active
