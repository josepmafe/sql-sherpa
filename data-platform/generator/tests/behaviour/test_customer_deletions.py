from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

import pytest
from helpers import SIMULATION_SEED, assert_rate, make_customer, make_snapshot, run_days
from sherpa_generator.behaviour.customer_deletions import (
    DELETE_AFTER_PURPOSE,
    DELETE_PURPOSE,
    CustomerDeletionSimulator,
)
from sherpa_generator.records import Customer
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.realism_rules import RealismRules

SIGNUP_DAY = date(2025, 1, 1)
CREATED_AT = datetime(2025, 1, 1, 9, 15, tzinfo=UTC)
CUSTOMERS = 10_000
DAYS = 210  # deletions fall 30-199 days after signup


@pytest.fixture(scope="module")
def deletions() -> list[tuple[int, datetime]]:
    """Every deletion over DAYS days of CUSTOMERS customers who all signed up on SIGNUP_DAY. Read-only."""
    return _deletions(_customers(CUSTOMERS), DAYS)


class TestCustomerDeletionSimulator:
    def test_deletion_rate_and_timing_window(self, deletions: list[tuple[int, datetime]]) -> None:
        delays = [(deleted_at.date() - CREATED_AT.date()).days for _, deleted_at in deletions]
        assert_rate(len(deletions), CUSTOMERS, 0.01)  # R6, Spec A §4.4: 1 % of customers
        assert all(30 <= delay <= 199 for delay in delays)
        assert min(delays) < 60
        assert max(delays) > 170

    def test_deletion_takes_effect_in_the_last_microsecond_of_its_day(
        self, deletions: list[tuple[int, datetime]]
    ) -> None:
        assert deletions
        for _, deleted_at in deletions:
            next_day = datetime.combine(deleted_at.date() + timedelta(days=1), time(), tzinfo=UTC)
            assert deleted_at == next_day - timedelta(microseconds=1)  # after every order and event of the day

    def test_deleted_customer_is_never_deleted_twice(self, deletions: list[tuple[int, datetime]]) -> None:
        assert deletions
        assert max(Counter(customer_id for customer_id, _ in deletions).values()) == 1

    def test_no_deletion_within_thirty_days_of_signup(self, deletions: list[tuple[int, datetime]]) -> None:
        within_thirty_days = [d for d in deletions if d[1].date() < SIGNUP_DAY + timedelta(days=30)]

        assert deletions
        assert within_thirty_days == []

    def test_update_sets_deleted_at_and_updated_at_to_the_same_instant(
        self, deletions: list[tuple[int, datetime]]
    ) -> None:
        changes = DayChanges()
        [(customer_id, deleted_at)] = deletions[:1]
        customer = make_customer(customer_id, created_at=CREATED_AT)

        CustomerDeletionSimulator(DeterministicRandom(SIMULATION_SEED)).simulate_day(
            make_snapshot(deleted_at.date(), customers=[customer]), changes
        )

        [update] = changes.updates
        assert (update.record_type, update.key) == (Customer, {"id": customer_id})
        assert update.values == {"deleted_at": deleted_at, "updated_at": deleted_at}
        assert changes.insert_counts() == {}

    @pytest.mark.parametrize(("first_fraction", "last_fraction", "delay"), [(0.0, 1 / 170, 30), (169 / 170, 1.0, 199)])
    def test_a_customer_drawn_at_the_edge_of_the_window_is_deleted_on_its_first_or_last_day(
        self, first_fraction: float, last_fraction: float, delay: int
    ) -> None:
        customer_id = _customer_id_where(first_fraction, last_fraction)
        customer = make_customer(customer_id, created_at=CREATED_AT)
        simulator = CustomerDeletionSimulator(DeterministicRandom(SIMULATION_SEED))

        deleted_on = [
            day
            for day in (SIGNUP_DAY + timedelta(days=d) for d in range(delay - 1, delay + 2))
            if _updates_on(simulator, day, customer)
        ]

        assert deleted_on == [SIGNUP_DAY + timedelta(days=delay)]


def _customer_id_where(first_fraction: float, last_fraction: float) -> int:
    """The first customer id R6 deletes whose delay fraction lies in [first_fraction, last_fraction)."""
    random = DeterministicRandom(SIMULATION_SEED)
    return next(
        i
        for i in range(1, 500_000)
        if random.entity_fraction("customer", i, DELETE_PURPOSE) < RealismRules.SOFT_DELETED_CUSTOMER_RATE
        and first_fraction <= random.entity_fraction("customer", i, DELETE_AFTER_PURPOSE) < last_fraction
    )


def _updates_on(simulator: CustomerDeletionSimulator, day: date, customer: Customer) -> bool:
    changes = DayChanges()

    simulator.simulate_day(make_snapshot(day, customers=[customer]), changes)

    return bool(changes.updates)


def _customers(count: int) -> list[Customer]:
    return [make_customer(i, created_at=CREATED_AT) for i in range(1, count + 1)]


def _deletions(customers: list[Customer], days: int) -> list[tuple[int, datetime]]:
    """Every deletion over `days` days of a shop whose customers never leave the snapshot."""
    simulator = CustomerDeletionSimulator(DeterministicRandom(SIMULATION_SEED))
    deletions: list[tuple[int, datetime]] = []
    for _, changes in run_days(
        simulator.simulate_day, make_snapshot(SIGNUP_DAY, customers=customers), SIGNUP_DAY, days
    ):
        for update in changes.updates:
            deleted_at = update.values["deleted_at"]
            assert isinstance(deleted_at, datetime)
            deletions.append((int(str(update.key["id"])), deleted_at))
    return deletions
