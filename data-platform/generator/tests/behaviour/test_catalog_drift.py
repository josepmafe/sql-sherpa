from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from helpers import SIMULATION_SEED, assert_rate, make_snapshot, run_days
from sherpa_generator.behaviour.catalog_drift import CatalogDriftSimulator
from sherpa_generator.records import Product, ProductVariant
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.realism_rules import RealismRules

if TYPE_CHECKING:
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

FIRST_DAY = date(2025, 3, 1)
DAYS = 100
VARIANTS = 1500
PRODUCTS = 1000
CREATED_AT = datetime(2025, 1, 1, tzinfo=UTC)


class TestCatalogDriftSimulator:
    def test_price_change_rate_and_updated_at_bumped(self) -> None:
        snapshot = _catalog()
        variants = {v.id: v for v in snapshot.variants}
        list_changes = cost_changes = 0
        for day, changes in run_days(_simulator().simulate_day, snapshot, FIRST_DAY, DAYS):
            day_end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
            for update in changes.updates:
                if update.record_type is not ProductVariant:
                    continue
                old = variants[int(str(update.key["id"]))]
                new_list, new_cost = update.values["list_price"], update.values["cost_price"]
                assert isinstance(new_list, Decimal)
                assert isinstance(new_cost, Decimal)
                assert set(update.values) == {"list_price", "cost_price", "updated_at"}
                assert day_end - timedelta(seconds=1) < update.values["updated_at"] < day_end  # type: ignore[operator]
                assert (new_list, new_cost) != (old.list_price, old.cost_price)
                if new_list != old.list_price:
                    list_changes += 1
                    assert Decimal("0.05") - Decimal("0.0001") <= abs(new_list / old.list_price - 1)
                    assert abs(new_list / old.list_price - 1) <= Decimal("0.20") + Decimal("0.0001")
                if new_cost != old.cost_price:
                    cost_changes += 1
                    assert Decimal("0.05") - Decimal("0.001") <= abs(new_cost / old.cost_price - 1)
                    assert abs(new_cost / old.cost_price - 1) <= Decimal("0.20") + Decimal("0.001")

        assert_rate(list_changes, VARIANTS * DAYS, 0.003)  # R10, Spec A §4.4: 0.3 % of variants a day
        assert_rate(cost_changes, VARIANTS * DAYS, 0.001)  # R10: 0.1 % a day

    def test_price_changes_go_both_ways(self) -> None:
        snapshot = _catalog()
        old = {v.id: v.list_price for v in snapshot.variants}

        ratios = [
            u.values["list_price"] / old[int(str(u.key["id"]))]  # type: ignore[operator]
            for _, changes in run_days(_simulator().simulate_day, snapshot, FIRST_DAY, DAYS)
            for u in changes.updates
            if u.record_type is ProductVariant
        ]

        assert any(ratio > 1 for ratio in ratios)
        assert any(ratio < 1 for ratio in ratios)

    def test_about_a_tenth_of_a_percent_of_products_toggle_is_active(self) -> None:
        snapshot = _catalog(inactive={1, 2, 3})
        active = {p.id: p.is_active for p in snapshot.products}

        toggles = [
            (u.key["id"], u.values)
            for _, c in run_days(_simulator().simulate_day, snapshot, FIRST_DAY, DAYS)
            for u in c.updates
            if u.record_type is Product
        ]

        assert 0.5 * 0.001 * PRODUCTS * DAYS <= len(toggles) <= 1.5 * 0.001 * PRODUCTS * DAYS
        assert all(set(values) == {"is_active", "updated_at"} for _, values in toggles)
        assert all(values["is_active"] is not active[int(str(key))] for key, values in toggles)

    def test_empty_catalog_writes_nothing(self) -> None:
        changes = DayChanges()

        _simulator().simulate_day(make_snapshot(FIRST_DAY), changes)

        assert changes.updates == []
        assert changes.insert_counts() == {}


def _simulator() -> CatalogDriftSimulator:
    return CatalogDriftSimulator(DeterministicRandom(SIMULATION_SEED), RealismRules())


def _catalog(inactive: set[int] | None = None) -> StoreSnapshot:
    products = [
        Product(
            id=product_id,
            category_id=1,
            name=f"Product {product_id}",
            brand="Pacer",
            is_active=product_id not in (inactive or set()),
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        for product_id in range(1, PRODUCTS + 1)
    ]
    variants = [
        ProductVariant(
            id=variant_id,
            product_id=1 + variant_id % PRODUCTS,
            sku=f"SKU-{variant_id}",
            size="M",
            color="Black",
            cost_price=Decimal("23.45"),
            list_price=Decimal("59.99"),
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        for variant_id in range(1, VARIANTS + 1)
    ]
    return make_snapshot(FIRST_DAY, products=products, variants=variants)
