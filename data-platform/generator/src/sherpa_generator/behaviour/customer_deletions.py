from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from sherpa_generator.records import Customer
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.realism_rules import RealismRules

if TYPE_CHECKING:
    from datetime import datetime

    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

MIN_DAYS_BEFORE_DELETION: Final = 30  # a deleted customer was deleted 30-199 days after signing up
DELETION_SPREAD_DAYS: Final = 170
DELETE_PURPOSE: Final = "delete"
DELETE_AFTER_PURPOSE: Final = "delete_after"
# A deletion takes effect in its day's last microsecond: the customer was in the day's morning snapshot, so their
# orders and events of that day all come before deleted_at, and the next day's snapshot no longer holds them.
DELETION_BEFORE_DAY_END: Final = timedelta(microseconds=1)
_CUSTOMER: Final = "customer"


class CustomerDeletionSimulator:
    """R6: soft-deletes (GDPR) 1 % of customers at the end of a day fixed per customer by `entity_fraction`.

    The snapshot holds only customers that are not deleted yet, so nobody is deleted twice.
    """

    def __init__(self, random: DeterministicRandom) -> None:
        self._random = random
        self._calendar = SimulationCalendar()
        self._deletions: dict[int, datetime | None] = {}  # customer id -> deletion time; fixed, so safe to cache

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        start, end = self._calendar.day_bounds(snapshot.day)
        for customer in snapshot.customers:
            deleted_at = self._deletion_time(customer)
            if deleted_at is not None and start <= deleted_at < end:
                changes.update(Customer, customer.id, deleted_at=deleted_at, updated_at=deleted_at)

    def _deletion_time(self, customer: Customer) -> datetime | None:
        if customer.id not in self._deletions:
            deleted_at = None
            if self._fraction(customer.id, DELETE_PURPOSE) < RealismRules.SOFT_DELETED_CUSTOMER_RATE:
                delay = MIN_DAYS_BEFORE_DELETION + int(
                    self._fraction(customer.id, DELETE_AFTER_PURPOSE) * DELETION_SPREAD_DAYS
                )
                deletion_day = (customer.created_at + timedelta(days=delay)).date()
                deleted_at = self._calendar.day_bounds(deletion_day)[1] - DELETION_BEFORE_DAY_END
            self._deletions[customer.id] = deleted_at

        return self._deletions[customer.id]

    def _fraction(self, customer_id: int, purpose: str) -> float:
        return self._random.entity_fraction(_CUSTOMER, customer_id, purpose)
