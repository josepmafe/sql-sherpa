from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final

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

if TYPE_CHECKING:
    import random

    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator

# Spec A §5.2 "Setup": category tree. An empty L3 list makes the L2 category a leaf (depth 2).
# 4 L1, 12 L2 and 25 L3 categories.
CATEGORY_TREE: Final[dict[str, dict[str, list[str]]]] = {
    "Footwear": {
        "Running Shoes": ["Road Running", "Trail Running", "Racing Flats"],
        "Casual Shoes": ["Sneakers", "Sandals", "Boots"],
        "Sports Shoes": [],
    },
    "Apparel": {
        "Tops": ["T-Shirts", "Jerseys", "Hoodies"],
        "Bottoms": ["Shorts", "Tights", "Trousers"],
        "Outerwear": [],
    },
    "Accessories": {
        "Bags": ["Backpacks", "Duffel Bags", "Hip Packs"],
        "Headwear": ["Caps", "Beanies", "Headbands"],
        "Socks": [],
    },
    "Equipment": {
        "Fitness": ["Dumbbells", "Yoga Mats", "Resistance Bands", "Jump Ropes"],
        "Outdoor": ["Tents", "Sleeping Bags", "Trekking Poles"],
        "Hydration": [],
    },
}

PRODUCTS_PER_ROOT: Final = {"Footwear": 25, "Apparel": 45, "Accessories": 115, "Equipment": 115}  # 300 products
BRANDS: Final = (
    "Aerolite", "Altura", "Bergwerk", "Cadence", "Corsa", "Drift", "Elevate", "Fjordic", "Gravel", "Halden",
    "Ironpeak", "Juno", "Kestrel", "Lumen", "Meridian", "Nordvik", "Orbit", "Pacer", "Quill", "Ridgeline",
    "Solace", "Tempo", "Umbra", "Vertex", "Wildmoor",
)  # fmt: skip
MODELS: Final = (
    "Pro", "Elite", "Core", "Flex", "Swift", "Trail", "Active", "Summit", "Rapid", "Classic", "Light", "Max",
)  # fmt: skip
COLOURS: Final = ("Black", "White", "Navy", "Red", "Grey", "Green", "Blue", "Orange")
MIN_COLOURS: Final = 1
MAX_COLOURS: Final = 3
FOOTWEAR_SIZES: Final = tuple(str(size) for size in range(38, 47))
APPAREL_SIZES: Final = ("XS", "S", "M", "L", "XL", "XXL")
ONE_SIZE: Final = ("one-size",)
SIZES_BY_ROOT: Final = {"Footwear": FOOTWEAR_SIZES, "Apparel": APPAREL_SIZES}
LIST_PRICE_RANGE_BY_ROOT: Final = {
    "Footwear": (30, 75),
    "Apparel": (10, 45),
    "Accessories": (5, 25),
    "Equipment": (12, 80),
}
PRICE_ENDINGS: Final = (Decimal("0.99"), Decimal("0.95"))
COST_RATIO_RANGE: Final = (0.30, 0.60)
SKU_BRAND_PREFIX_LENGTH: Final = 3

ALWAYS_ON_CHANNELS: Final = (CampaignChannel.PAID_SEARCH, CampaignChannel.PAID_SOCIAL, CampaignChannel.AFFILIATE)
# Seasonal campaigns: (name, channel, (start month, day), (end month, day)); Black Friday ones follow the calendar.
SEASONAL_CAMPAIGNS: Final = (
    ("Spring Sale", CampaignChannel.PAID_SEARCH, (3, 15), (4, 30)),
    ("Summer Sale", CampaignChannel.PAID_SEARCH, (6, 15), (8, 15)),
    ("Back to School", CampaignChannel.PAID_SOCIAL, (8, 15), (9, 30)),
    ("Winter Clearance", CampaignChannel.PAID_SOCIAL, (1, 5), (2, 15)),
    ("Spring Newsletter", CampaignChannel.EMAIL, (3, 15), (3, 31)),
    ("Summer Newsletter", CampaignChannel.EMAIL, (6, 15), (6, 30)),
    ("Autumn Newsletter", CampaignChannel.EMAIL, (10, 1), (10, 15)),
    ("Christmas Newsletter", CampaignChannel.EMAIL, (12, 1), (12, 20)),
    ("New Year Newsletter", CampaignChannel.EMAIL, (1, 2), (1, 15)),
)
BLACK_FRIDAY_CAMPAIGNS: Final = (
    ("Black Friday Search", CampaignChannel.PAID_SEARCH),
    ("Black Friday Social", CampaignChannel.PAID_SOCIAL),
    ("Black Friday Email", CampaignChannel.EMAIL),
)
BLACK_FRIDAY_PREFIX: Final = "Black Friday"
BLACK_FRIDAY_PROMOTION_CAMPAIGN: Final = "Black Friday Email"
BLACK_FRIDAY_PROMOTION_NAME: Final = "Black Friday 20%"
BLACK_FRIDAY_PERCENT: Final = Decimal(20)
CAMPAIGN_BUDGET_STEPS: Final = (20, 400)  # x BUDGET_STEP => EUR 2k-40k
BUDGET_STEP: Final = 100

SEASONAL_PERCENTS: Final = (10, 15, 20, 25, 30)
FIXED_DISCOUNTS: Final = ((Decimal(5), Decimal(50)), (Decimal(10), Decimal(75)), (Decimal(15), Decimal(100)))
FIXED_HALVES: Final = 2
FREE_SHIPPING_QUARTERS: Final = (1, 3)  # zero-based quarters of the range: Q2 and Q4
RANGE_QUARTERS: Final = 4
MIN_COUPONS: Final = 1
MAX_COUPONS: Final = 3
COUPON_CODE_LENGTH: Final = 8


@dataclass(frozen=True, slots=True)
class _CampaignWindow:
    name: str
    channel: CampaignChannel
    first_day: date
    last_day: date


class CatalogBuilder:
    """One-off setup of the catalog, campaigns, promotions and coupons. Not scaled: scale only affects daily volumes."""

    def __init__(self, random: DeterministicRandom, keys: PrimaryKeyAllocator) -> None:
        self._random = random
        self._keys = keys
        self._calendar = SimulationCalendar()
        self._used_codes: set[str] = set()

    def build(self, changes: DayChanges, start_date: date, end_date: date) -> None:
        # The day before the first simulated day gives the setup its own RNG stream.
        rng = self._random.for_day(start_date - timedelta(days=1))
        self._start = start_date
        self._end = end_date
        self._created_at = self._calendar.day_bounds(start_date)[0]

        leaves_by_root = self._insert_categories(changes)
        self._insert_products(changes, rng, leaves_by_root)
        seasonal = self._insert_campaigns(changes, rng)
        self._insert_promotions(changes, rng, seasonal)

    def _insert_categories(self, changes: DayChanges) -> dict[str, list[Category]]:
        leaves_by_root: dict[str, list[Category]] = {}
        for root_name, level_two in CATEGORY_TREE.items():
            root = self._category(changes, root_name, None)
            leaves = leaves_by_root.setdefault(root_name, [])
            for name, level_three in level_two.items():
                category = self._category(changes, name, root.id)
                if not level_three:
                    leaves.append(category)
                leaves.extend(self._category(changes, leaf, category.id) for leaf in level_three)

        return leaves_by_root

    def _category(self, changes: DayChanges, name: str, parent_id: int | None) -> Category:
        category = Category(id=self._keys.next_id(Category), parent_id=parent_id, name=name)
        changes.insert(category)
        return category

    def _insert_products(
        self, changes: DayChanges, rng: random.Random, leaves_by_root: dict[str, list[Category]]
    ) -> None:
        brand_index = 0
        for root_name, count in PRODUCTS_PER_ROOT.items():
            leaves = leaves_by_root[root_name]
            low, high = LIST_PRICE_RANGE_BY_ROOT[root_name]
            sizes = SIZES_BY_ROOT.get(root_name, ONE_SIZE)
            for index in range(count):
                leaf = leaves[index % len(leaves)]  # every leaf gets products
                brand = BRANDS[brand_index % len(BRANDS)]  # every brand gets products
                brand_index += 1
                product = Product(
                    id=self._keys.next_id(Product),
                    category_id=leaf.id,
                    name=f"{brand} {rng.choice(MODELS)} {leaf.name}",
                    brand=brand,
                    is_active=True,
                    created_at=self._created_at,
                    updated_at=self._created_at,
                )
                changes.insert(product)
                list_price = Decimal(rng.randint(low, high - 1)) + rng.choice(PRICE_ENDINGS)
                self._insert_variants(changes, rng, product, sizes, list_price)

    def _insert_variants(
        self, changes: DayChanges, rng: random.Random, product: Product, sizes: tuple[str, ...], list_price: Decimal
    ) -> None:
        colours = rng.sample(COLOURS, k=rng.randint(MIN_COLOURS, MAX_COLOURS))
        prefix = product.brand[:SKU_BRAND_PREFIX_LENGTH].upper()
        for colour in colours:
            for size in sizes:
                cost_ratio = Decimal(str(round(rng.uniform(*COST_RATIO_RANGE), 4)))
                changes.insert(
                    ProductVariant(
                        id=self._keys.next_id(ProductVariant),
                        product_id=product.id,
                        sku=f"{prefix}-{product.id:04d}-{size}-{colour.upper()}",
                        size=size,
                        color=colour,
                        cost_price=list_price * cost_ratio,
                        list_price=list_price,
                        created_at=self._created_at,
                        updated_at=self._created_at,
                    )
                )

    def _insert_campaigns(self, changes: DayChanges, rng: random.Random) -> list[Campaign]:
        for channel in ALWAYS_ON_CHANNELS:
            name = f"Always On {channel.value.replace('_', ' ').title()}"
            self._campaign(changes, rng, _CampaignWindow(name, channel, self._start, self._end))

        return [self._campaign(changes, rng, window) for window in self._seasonal_windows()]

    def _seasonal_windows(self) -> list[_CampaignWindow]:
        windows: list[_CampaignWindow] = []
        for year in range(self._start.year, self._end.year + 1):
            for name, channel, (start_month, start_day), (end_month, end_day) in SEASONAL_CAMPAIGNS:
                first_day = date(year, start_month, start_day)
                last_day = date(year, end_month, end_day)
                windows.append(_CampaignWindow(f"{name} {year}", channel, first_day, last_day))

            first, last = self._calendar.black_friday_week(year)
            windows.extend(
                _CampaignWindow(f"{name} {year}", channel, first, last) for name, channel in BLACK_FRIDAY_CAMPAIGNS
            )

        return [clipped for window in windows if (clipped := self._clip(window)) is not None]

    def _clip(self, window: _CampaignWindow) -> _CampaignWindow | None:
        """Restrict a window to the simulated range; None when they do not overlap."""
        first, last = max(window.first_day, self._start), min(window.last_day, self._end)
        if first > last:
            return None

        return _CampaignWindow(window.name, window.channel, first, last)

    def _campaign(self, changes: DayChanges, rng: random.Random, window: _CampaignWindow) -> Campaign:
        low, high = CAMPAIGN_BUDGET_STEPS
        campaign = Campaign(
            id=self._keys.next_id(Campaign),
            name=window.name,
            utm_campaign=re.sub(r"[^a-z0-9]+", "-", window.name.lower()).strip("-"),
            channel=window.channel,
            starts_at=self._midnight(window.first_day),
            ends_at=self._midnight(window.last_day + timedelta(days=1)),
            budget=Decimal(rng.randint(low, high) * BUDGET_STEP),
        )
        changes.insert(campaign)
        return campaign

    def _insert_promotions(self, changes: DayChanges, rng: random.Random, seasonal: list[Campaign]) -> None:
        for campaign in seasonal:
            if campaign.name.startswith(BLACK_FRIDAY_PREFIX):
                if campaign.name.startswith(BLACK_FRIDAY_PROMOTION_CAMPAIGN):
                    self._campaign_promotion(changes, rng, campaign, BLACK_FRIDAY_PROMOTION_NAME, BLACK_FRIDAY_PERCENT)
                continue

            percent = Decimal(rng.choice(SEASONAL_PERCENTS))
            self._campaign_promotion(changes, rng, campaign, f"{campaign.name} {percent}%", percent)

        self._insert_range_promotions(changes, rng)

    def _insert_range_promotions(self, changes: DayChanges, rng: random.Random) -> None:
        """Fixed-amount and free-shipping promotions are spread over the range, not tied to a campaign."""
        days = (self._end - self._start).days + 1
        for half in range(FIXED_HALVES):
            window = self._range_window(days * half // FIXED_HALVES, days * (half + 1) // FIXED_HALVES)
            for amount, minimum in FIXED_DISCOUNTS:
                name = f"Save {amount} EUR over {minimum} EUR (H{half + 1})"
                self._promotion(changes, rng, name, PromotionType.FIXED, amount, minimum, window)

        for quarter in FREE_SHIPPING_QUARTERS:
            window = self._range_window(days * quarter // RANGE_QUARTERS, days * (quarter + 1) // RANGE_QUARTERS)
            name = f"Free Shipping Q{quarter + 1}"
            self._promotion(changes, rng, name, PromotionType.FREE_SHIPPING, Decimal(0), Decimal(0), window)

    def _range_window(self, first_offset: int, end_offset: int) -> tuple[datetime, datetime]:
        """[first_offset, end_offset) days into the range, at least one day long and inside the range."""
        first = min(self._start + timedelta(days=first_offset), self._end)
        last = max(first, min(self._start + timedelta(days=end_offset - 1), self._end))
        return self._midnight(first), self._midnight(last + timedelta(days=1))

    def _campaign_promotion(
        self, changes: DayChanges, rng: random.Random, campaign: Campaign, name: str, percent: Decimal
    ) -> None:
        window = (campaign.starts_at, campaign.ends_at)
        self._promotion(changes, rng, name, PromotionType.PERCENT, percent, Decimal(0), window, campaign)

    def _promotion(
        self,
        changes: DayChanges,
        rng: random.Random,
        name: str,
        promotion_type: PromotionType,
        value: Decimal,
        min_order_value: Decimal,
        window: tuple[datetime, datetime],
        campaign: Campaign | None = None,
    ) -> None:
        promotion = Promotion(
            id=self._keys.next_id(Promotion),
            name=name,
            type=promotion_type,
            value=value,
            min_order_value=min_order_value,
            campaign_id=campaign.id if campaign else None,
            starts_at=window[0],
            ends_at=window[1],
        )
        changes.insert(promotion)
        if campaign is not None and campaign.channel == CampaignChannel.EMAIL:
            for _ in range(rng.randint(MIN_COUPONS, MAX_COUPONS)):
                changes.insert(
                    CouponCode(
                        id=self._keys.next_id(CouponCode),
                        promotion_id=promotion.id,
                        code=self._coupon_code(rng),
                        max_redemptions=None,
                        is_single_use=False,
                    )
                )

    def _coupon_code(self, rng: random.Random) -> str:
        while True:
            code = "".join(rng.choices(string.ascii_uppercase, k=COUPON_CODE_LENGTH))
            if code not in self._used_codes:
                self._used_codes.add(code)
                return code

    def _midnight(self, day: date) -> datetime:
        return self._calendar.day_bounds(day)[0]
