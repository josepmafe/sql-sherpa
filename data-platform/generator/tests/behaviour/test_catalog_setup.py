from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.records import (
    Campaign,
    CampaignChannel,
    Category,
    CouponCode,
    Product,
    ProductVariant,
    Promotion,
    PromotionType,
)
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator

SEED = 42
START = date(2025, 1, 1)
END = date(2025, 12, 31)


def build(start: date = START, end: date = END, seed: int = SEED) -> DayChanges:
    changes = DayChanges()
    CatalogBuilder(DeterministicRandom(seed), PrimaryKeyAllocator({})).build(changes, start, end)
    return changes


class TestCatalogBuilder:
    def test_category_tree_has_four_roots_max_depth_three_and_products_on_leaves(self) -> None:
        changes = build()
        categories = {c.id: c for c in changes.inserted(Category)}
        parents = {c.parent_id for c in categories.values() if c.parent_id is not None}

        roots = [c for c in categories.values() if c.parent_id is None]
        depths = {c.id: self._depth(categories, c.id) for c in categories.values()}
        leaves = {cid for cid in categories if cid not in parents}

        assert sorted(c.name for c in roots) == ["Accessories", "Apparel", "Equipment", "Footwear"]
        assert sum(1 for d in depths.values() if d == 2) == 12
        assert sum(1 for d in depths.values() if d == 3) == 25
        assert max(depths.values()) == 3
        assert any(depths[leaf] == 2 for leaf in leaves)
        assert {p.category_id for p in changes.inserted(Product)} <= leaves

    def test_catalog_sizes_and_unique_skus(self) -> None:
        changes = build()

        products = changes.inserted(Product)
        variants = changes.inserted(ProductVariant)
        skus = [v.sku for v in variants]

        assert len(products) == 300
        assert len({p.brand for p in products}) == 25
        assert all(p.name.startswith(f"{p.brand} ") for p in products)
        assert 1275 <= len(variants) <= 1725
        assert len(set(skus)) == len(skus)
        assert {v.product_id for v in variants} == {p.id for p in products}

    def test_variant_sizes_and_list_prices_follow_the_root_category(self) -> None:
        changes = build()
        categories = {c.id: c for c in changes.inserted(Category)}
        root_of = {p.id: self._root(categories, p.category_id).name for p in changes.inserted(Product)}
        ranges = {
            "Footwear": (30, 75),
            "Apparel": (10, 45),
            "Accessories": (5, 25),
            "Equipment": (12, 80),
        }

        for variant in changes.inserted(ProductVariant):
            root = root_of[variant.product_id]
            low, high = ranges[root]
            assert low <= variant.list_price <= high
            assert variant.list_price % 1 in {Decimal("0.99"), Decimal("0.95")}
            if root == "Footwear":
                assert variant.size in {str(n) for n in range(38, 47)}
            elif root == "Apparel":
                assert variant.size in {"XS", "S", "M", "L", "XL", "XXL"}
            else:
                assert variant.size == "one-size"

    def test_cost_ratio_bounds(self) -> None:
        changes = build()

        for variant in changes.inserted(ProductVariant):
            # one cent of slack for rounding the cost to two decimals
            assert variant.list_price * Decimal("0.30") - Decimal("0.01") <= variant.cost_price
            assert variant.cost_price <= variant.list_price * Decimal("0.60") + Decimal("0.01")

    def test_black_friday_promotion_matches_calendar_week(self) -> None:
        changes = build()
        first, last = SimulationCalendar().black_friday_week(2025)

        promotion = next(p for p in changes.inserted(Promotion) if p.name == "Black Friday 20%")

        assert promotion.type == PromotionType.PERCENT
        assert promotion.value == Decimal("20.00")
        assert promotion.starts_at.date() == first
        assert (promotion.ends_at.date() - last).days == 1

    def test_black_friday_promotion_covers_week_crossing_into_december(self) -> None:
        changes = build(date(2024, 1, 1), date(2024, 12, 31))

        promotion = next(p for p in changes.inserted(Promotion) if p.name == "Black Friday 20%")

        assert promotion.starts_at.date() == date(2024, 11, 25)
        assert promotion.ends_at.date() == date(2024, 12, 2)

    def test_promotion_mix(self) -> None:
        changes = build()
        promotions = changes.inserted(Promotion)
        campaigns = {c.id: c for c in changes.inserted(Campaign)}
        coupons = changes.inserted(CouponCode)

        fixed = [p for p in promotions if p.type == PromotionType.FIXED]
        free_shipping = [p for p in promotions if p.type == PromotionType.FREE_SHIPPING]
        percent = [p for p in promotions if p.type == PromotionType.PERCENT]
        min_by_value = {p.value: p.min_order_value for p in fixed}

        assert 15 <= len(promotions) <= 25
        assert len(free_shipping) == 2
        assert min_by_value == {
            Decimal("5.00"): Decimal("50.00"),
            Decimal("10.00"): Decimal("75.00"),
            Decimal("15.00"): Decimal("100.00"),
        }
        assert all(Decimal(10) <= p.value <= Decimal(30) for p in percent)
        for promotion in promotions:
            own = [c for c in coupons if c.promotion_id == promotion.id]
            campaign = campaigns.get(promotion.campaign_id) if promotion.campaign_id else None
            if campaign is not None and campaign.channel == CampaignChannel.EMAIL:
                assert 1 <= len(own) <= 3
            else:
                assert own == []
        assert all(len(c.code) == 8 and c.code.isalpha() and c.code.isupper() for c in coupons)
        assert len({c.code for c in coupons}) == len(coupons)

    def test_always_on_campaigns_cover_range(self) -> None:
        changes = build()

        always_on = [c for c in changes.inserted(Campaign) if c.name.startswith("Always On")]

        assert {c.channel for c in always_on} == {
            CampaignChannel.PAID_SEARCH,
            CampaignChannel.PAID_SOCIAL,
            CampaignChannel.AFFILIATE,
        }
        for campaign in always_on:
            assert campaign.starts_at.date() == START
            assert campaign.ends_at.date() > END

    def test_campaign_mix_and_attributes(self) -> None:
        changes = build()
        campaigns = changes.inserted(Campaign)

        assert 14 <= len(campaigns) <= 16
        assert sum(1 for c in campaigns if c.channel == CampaignChannel.EMAIL) >= 4
        assert all(c.channel != CampaignChannel.DISPLAY for c in campaigns)
        assert all(Decimal(2000) <= c.budget <= Decimal(40000) for c in campaigns)
        assert all(c.utm_campaign == c.name.lower().replace(" ", "-") for c in campaigns)
        assert len({c.utm_campaign for c in campaigns}) == len(campaigns)
        assert all(c.starts_at < c.ends_at for c in campaigns)

    def test_build_is_deterministic(self) -> None:
        first = build()
        second = build()

        assert first.inserts == second.inserts

    def test_different_seeds_differ(self) -> None:
        assert build(seed=1).inserted(ProductVariant) != build(seed=2).inserted(ProductVariant)

    def test_short_range_without_november_has_no_black_friday_promotion(self) -> None:
        changes = build(date(2025, 1, 1), date(2025, 3, 31))

        names = [p.name for p in changes.inserted(Promotion)] + [c.name for c in changes.inserted(Campaign)]

        assert not any("Black Friday" in n for n in names)
        for campaign in changes.inserted(Campaign):
            assert campaign.starts_at.date() >= date(2025, 1, 1)
            assert campaign.ends_at.date() <= date(2025, 4, 1)

    @pytest.mark.parametrize("last_day", [1, 3])
    def test_tiny_range_still_yields_non_empty_windows(self, last_day: int) -> None:
        changes = build(date(2025, 6, 1), date(2025, 6, last_day))

        assert all(p.starts_at < p.ends_at for p in changes.inserted(Promotion))
        assert all(c.starts_at < c.ends_at for c in changes.inserted(Campaign))

    @staticmethod
    def _depth(categories: dict[int, Category], category_id: int) -> int:
        parent = categories[category_id].parent_id
        return 1 if parent is None else 1 + TestCatalogBuilder._depth(categories, parent)

    @staticmethod
    def _root(categories: dict[int, Category], category_id: int) -> Category:
        category = categories[category_id]
        return category if category.parent_id is None else TestCatalogBuilder._root(categories, category.parent_id)
