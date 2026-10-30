from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import AFTER_SALES_DAYS, AFTER_SALES_START, SIMULATION_SEED
from sherpa_generator.behaviour.after_sales import GOODWILL_DELAY_DAYS, RECEIPT_DELAY_DAYS, REQUEST_DELAY_DAYS
from sherpa_generator.records import OrderStatus, PaymentStatus, ReturnStatus
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.store_snapshot import RECENT_DELIVERY_DAYS, StoreSnapshotLoader, lognormal_propensity

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

MID_MONTH = date(2025, 3, 15)
LAST_DAY = date(2025, 3, 30)
DELETED_AT = datetime(2025, 3, 20, tzinfo=UTC)


@pytest.mark.integration
class TestStoreSnapshotLoader:
    def test_lists_are_ordered_by_key_and_exclude_deleted_customers_from_visitors(self, month: Database) -> None:
        with month.connect() as conn:
            all_ids = [row[0] for row in conn.execute("SELECT id FROM raw_shop.customers").fetchall()]
            deleted_id = min(all_ids)
            conn.execute("UPDATE raw_shop.customers SET deleted_at = %s WHERE id = %s", [DELETED_AT, deleted_id])

            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)
            conn.rollback()

        customer_ids = [c.id for c in snapshot.customers]
        variant_ids = [v.id for v in snapshot.variants]
        lead_ids = [lead.id for lead in snapshot.leads]
        send_ids = [send.id for send in snapshot.recent_email_sends]
        campaign_ids = [[c.id for c in campaigns] for campaigns in snapshot.active_campaigns.values()]

        assert customer_ids == sorted(customer_ids)
        assert len(customer_ids) == len(all_ids) - 1
        assert deleted_id not in customer_ids
        assert deleted_id not in snapshot.customer_devices
        assert set(snapshot.customer_devices) <= set(customer_ids)
        assert all(devices == sorted(devices) for devices in snapshot.customer_devices.values())
        assert variant_ids
        assert variant_ids == sorted(variant_ids)
        assert lead_ids
        assert lead_ids == sorted(lead_ids)
        assert send_ids
        assert send_ids == sorted(send_ids)
        assert campaign_ids
        assert all(ids == sorted(ids) for ids in campaign_ids)

    def test_order_state_is_ordered_by_key_and_holds_only_what_can_still_change(self, month: Database) -> None:
        with month.connect() as conn:
            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)
            [(orders, open_orders, addresses)] = conn.execute(
                "SELECT (SELECT count(*) FROM raw_shop.orders),"
                " (SELECT count(*) FROM raw_shop.orders WHERE status IN ('placed', 'paid', 'shipped')),"
                " (SELECT count(*) FROM raw_shop.addresses WHERE type = 'shipping' AND is_default)"
            ).fetchall()

        start = datetime(2025, 3, 30, tzinfo=UTC)
        open_ids = {o.id for o in snapshot.open_orders}
        paid_ids = {o.id for o in snapshot.open_orders if o.status == OrderStatus.PAID}
        promotion_ids = {p.id for p in snapshot.active_promotions}
        lists = [
            snapshot.open_orders,
            snapshot.open_shipments,
            snapshot.captured_payments,
            snapshot.active_promotions,
            snapshot.coupon_codes,
        ]

        assert all(ids == sorted(ids) for ids in ([r.id for r in records] for records in lists))
        assert 0 < len(open_ids) == open_orders < orders
        assert {OrderStatus.PAID, OrderStatus.SHIPPED} <= {o.status for o in snapshot.open_orders}
        assert {o.status for o in snapshot.open_orders} <= {OrderStatus.PLACED, OrderStatus.PAID, OrderStatus.SHIPPED}
        assert {s.order_id for s in snapshot.open_shipments} <= open_ids
        assert all(s.delivered_at is None for s in snapshot.open_shipments)
        assert {p.order_id for p in snapshot.captured_payments} == paid_ids
        assert all(p.status == PaymentStatus.CAPTURED for p in snapshot.captured_payments)
        assert snapshot.active_promotions
        assert all(p.starts_at < start + timedelta(days=1) and p.ends_at > start for p in snapshot.active_promotions)
        assert snapshot.coupon_codes
        assert {c.promotion_id for c in snapshot.coupon_codes} <= promotion_ids
        assert len(snapshot.shipping_address_ids) == addresses
        assert list(snapshot.shipping_address_ids) == sorted(snapshot.shipping_address_ids)

    def test_after_sales_state_holds_forty_days_of_deliveries_and_the_open_returns(
        self, after_sales_season: Database
    ) -> None:
        next_day = AFTER_SALES_START + timedelta(days=AFTER_SALES_DAYS)  # the day after the run
        later_day = next_day + timedelta(days=20)  # its window misses the run's first deliveries
        with after_sales_season.connect() as conn:
            loader = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED))
            snapshot, later = loader.load(next_day), loader.load(later_day)
            [(delivered, delivered_at_mismatches)] = conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE o.updated_at <> s.delivered_at)"
                " FROM raw_shop.orders o JOIN raw_shop.shipments s ON s.order_id = o.id WHERE o.status = 'delivered'"
            ).fetchall()

        later_start = datetime(later_day.year, later_day.month, later_day.day, tzinfo=UTC)
        orders = snapshot.recently_delivered_orders
        order_ids = {o.id for o in orders}
        lists = [
            snapshot.products,
            orders,
            snapshot.recently_delivered_items,
            snapshot.recently_delivered_payments,
            snapshot.open_returns,
        ]

        assert all(ids == sorted(ids) for ids in ([r.id for r in records] for records in lists))
        assert orders
        assert 0 < len(later.recently_delivered_orders) < delivered
        assert all(later_start - timedelta(days=40) <= o.updated_at for o in later.recently_delivered_orders)
        assert delivered_at_mismatches == 0  # a delivered order's updated_at is its delivery time
        assert all(o.status == OrderStatus.DELIVERED for o in orders)
        assert {i.order_id for i in snapshot.recently_delivered_items} == order_ids
        assert {p.order_id for p in snapshot.recently_delivered_payments} == order_ids
        assert all(p.status == PaymentStatus.CAPTURED for p in snapshot.recently_delivered_payments)
        assert snapshot.open_returns
        assert {r.status for r in snapshot.open_returns} <= {ReturnStatus.REQUESTED, ReturnStatus.APPROVED}
        assert {r.order_id for r in snapshot.open_returns} <= order_ids
        assert list(snapshot.product_category_l1) == [p.id for p in snapshot.products]
        assert Counter(snapshot.product_category_l1.values()) == {
            "Footwear": 25,
            "Apparel": 45,
            "Accessories": 115,
            "Equipment": 115,
        }

    def test_variant_popularity_sums_to_one_and_skips_inactive_products(self, month: Database) -> None:
        with month.connect() as conn:
            inactive_product = 1
            conn.execute("UPDATE raw_shop.products SET is_active = false WHERE id = %s", [inactive_product])

            active_products = {row[0] for row in conn.execute("SELECT id FROM raw_shop.products WHERE is_active")}
            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)
            conn.rollback()

        inactive_variants = {v.id for v in snapshot.variants if v.product_id == inactive_product}
        assert inactive_variants
        assert inactive_variants.isdisjoint(snapshot.variant_popularity)
        # catalog drift has deactivated other products in the run: they are skipped too
        assert set(snapshot.variant_popularity) == {v.id for v in snapshot.variants if v.product_id in active_products}
        assert sum(snapshot.variant_popularity.values()) == pytest.approx(1.0)
        assert max(snapshot.variant_popularity.values()) > 20 * min(snapshot.variant_popularity.values())

    def test_propensity_is_stable_per_customer(self, month: Database) -> None:
        with month.connect() as conn:
            early = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(MID_MONTH)
            late = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)

        common = [c.id for c in early.customers]
        values = [late.propensity(customer_id) for customer_id in common]

        assert common
        assert [early.propensity(customer_id) for customer_id in common] == values
        assert all(value > 0 for value in values)
        assert len(set(values)) == len(values)

    def test_snapshot_before_any_activity_has_catalog_but_no_people(self, temp_database: Database) -> None:
        with temp_database.connect() as conn:
            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)

        assert snapshot.variants == []
        assert snapshot.variant_popularity == {}
        assert snapshot.customers == []
        assert snapshot.customer_devices == {}
        assert snapshot.leads == []
        assert snapshot.active_campaigns == {}
        assert snapshot.recent_email_sends == []
        assert snapshot.active_promotions == []
        assert snapshot.coupon_codes == []
        assert snapshot.shipping_address_ids == {}
        assert snapshot.open_orders == []
        assert snapshot.open_shipments == []
        assert snapshot.captured_payments == []
        assert snapshot.products == []
        assert snapshot.product_category_l1 == {}
        assert snapshot.recently_delivered_orders == []
        assert snapshot.recently_delivered_items == []
        assert snapshot.recently_delivered_payments == []
        assert snapshot.open_returns == []


def test_lognormal_propensity_median_and_sigma() -> None:
    assert lognormal_propensity(0.5) == pytest.approx(1.0)
    assert lognormal_propensity(0.841344746) == pytest.approx(math.exp(1.0), rel=1e-6)


def test_lognormal_propensity_is_finite_at_fraction_zero() -> None:
    value = lognormal_propensity(0.0)

    assert 0.0 < value < lognormal_propensity(0.001)


def test_delivery_window_covers_every_after_sales_delay() -> None:
    latest_receipt = REQUEST_DELAY_DAYS[1] + RECEIPT_DELAY_DAYS[1]

    assert latest_receipt <= RECENT_DELIVERY_DAYS
    assert GOODWILL_DELAY_DAYS[1] <= RECENT_DELIVERY_DAYS
