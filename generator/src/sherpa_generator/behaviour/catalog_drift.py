from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from sherpa_generator.records import Product, ProductVariant
from sherpa_generator.records.base import quantize_money
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.deterministic_random import uniform_decimal

if TYPE_CHECKING:
    import random
    from decimal import Decimal

    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.realism_rules import RealismRules
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

STREAM: Final = "drift"
PRODUCT_TOGGLE_RATE: Final = 0.001  # Spec A §5.2 step 1: ~0.1 % of products activated or deactivated per day
PRICE_INCREASE_SHARE: Final = 0.5  # R10 says +/- 5-20 %: as many price rises as cuts
# The nightly catalog update lands in the day's last microsecond: after every order of the day (all priced from the
# morning snapshot, so unit_price is the list price at the time of order) and never beyond the simulated day.
UPDATE_BEFORE_DAY_END: Final = timedelta(microseconds=1)


class CatalogDriftSimulator:
    """Spec A §5.2 step 1: R10 price changes and products going in and out of the range."""

    def __init__(self, random: DeterministicRandom, rules: RealismRules) -> None:
        self._random = random
        self._rules = rules
        self._calendar = SimulationCalendar()

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        rng = self._random.for_day(snapshot.day, STREAM)
        updated_at = self._calendar.day_bounds(snapshot.day)[1] - UPDATE_BEFORE_DAY_END
        # One update shape per table, so the writer sends each as a single batch.
        for variant in snapshot.variants:
            list_price = self._drifted(variant.list_price, self._rules.LIST_PRICE_CHANGE_RATE, rng)
            cost_price = self._drifted(variant.cost_price, self._rules.COST_PRICE_CHANGE_RATE, rng)
            if (list_price, cost_price) != (variant.list_price, variant.cost_price):
                changes.update(
                    ProductVariant, variant.id, list_price=list_price, cost_price=cost_price, updated_at=updated_at
                )

        for product in snapshot.products:
            if rng.random() < PRODUCT_TOGGLE_RATE:
                changes.update(Product, product.id, is_active=not product.is_active, updated_at=updated_at)

    def _drifted(self, price: Decimal, rate: float, rng: random.Random) -> Decimal:
        """The price, or with probability `rate` the price moved up or down by 5-20 % (R10)."""
        if rng.random() >= rate:
            return price

        share = uniform_decimal(rng, self._rules.PRICE_CHANGE_MIN_SHARE, self._rules.PRICE_CHANGE_MAX_SHARE)
        return quantize_money(price * (1 + share if rng.random() < PRICE_INCREASE_SHARE else 1 - share))
