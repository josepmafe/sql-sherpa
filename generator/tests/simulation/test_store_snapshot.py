from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import SIMULATION_SEED
from sherpa_generator.records import OrderStatus, PaymentStatus
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.store_snapshot import StoreSnapshotLoader, lognormal_propensity

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

MID_MONTH = date(2025, 3, 15)
LAST_DAY = date(2025, 3, 30)
DELETED_AT = datetime(2025, 3, 20, tzinfo=UTC)


@pytest.mark.integration
class TestStoreSnapshotLoader:
    def test_lists_are_ordered_by_key_and_exclude_deleted_customers_from_visitors(
        self, traffic_month: Database
    ) -> None:
        with traffic_month.connect() as conn:
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

    def test_order_state_is_ordered_by_key_and_holds_only_what_can_still_change(self, order_month: Database) -> None:
        with order_month.connect() as conn:
            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)
            [(orders, addresses)] = conn.execute(
                "SELECT (SELECT count(*) FROM raw_shop.orders),"
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
        assert 0 < len(open_ids) < orders
        assert {o.status for o in snapshot.open_orders} == {OrderStatus.PLACED, OrderStatus.PAID, OrderStatus.SHIPPED}
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

    def test_variant_popularity_sums_to_one_and_skips_inactive_products(self, traffic_month: Database) -> None:
        with traffic_month.connect() as conn:
            inactive_product = 1
            conn.execute("UPDATE raw_shop.products SET is_active = false WHERE id = %s", [inactive_product])

            snapshot = StoreSnapshotLoader(conn, DeterministicRandom(SIMULATION_SEED)).load(LAST_DAY)
            conn.rollback()

        inactive_variants = {v.id for v in snapshot.variants if v.product_id == inactive_product}
        assert inactive_variants
        assert inactive_variants.isdisjoint(snapshot.variant_popularity)
        assert set(snapshot.variant_popularity) == {v.id for v in snapshot.variants} - inactive_variants
        assert sum(snapshot.variant_popularity.values()) == pytest.approx(1.0)
        assert max(snapshot.variant_popularity.values()) > 20 * min(snapshot.variant_popularity.values())

    def test_propensity_is_stable_per_customer(self, traffic_month: Database) -> None:
        with traffic_month.connect() as conn:
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


def test_lognormal_propensity_median_and_sigma() -> None:
    assert lognormal_propensity(0.5) == pytest.approx(1.0)
    assert lognormal_propensity(0.841344746) == pytest.approx(math.exp(1.1), rel=1e-6)


def test_lognormal_propensity_is_finite_at_fraction_zero() -> None:
    value = lognormal_propensity(0.0)

    assert 0.0 < value < lognormal_propensity(0.001)
