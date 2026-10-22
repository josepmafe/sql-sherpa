from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta

from helpers import SIMULATION_SEED, make_customer, make_snapshot
from sherpa_generator.behaviour.customer_deletions import CustomerDeletionSimulator
from sherpa_generator.records import Customer
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom

SIGNUP_DAY = date(2025, 1, 1)
CREATED_AT = datetime(2025, 1, 1, 9, 15, tzinfo=UTC)
CUSTOMERS = 10_000
DAYS = 210  # deletions fall 30-199 days after signup


class TestCustomerDeletionSimulator:
    def test_deletion_rate_and_timing_window(self) -> None:
        customers = _customers(CUSTOMERS)

        deletions = _deletions(customers, DAYS)

        delays = [deleted_at - CREATED_AT for _, deleted_at in deletions]
        assert 0.008 * CUSTOMERS <= len(deletions) <= 0.012 * CUSTOMERS  # R6: 1 %
        assert all(timedelta(days=30) <= delay <= timedelta(days=199) for delay in delays)
        assert all(delay % timedelta(days=1) == timedelta(0) for delay in delays)  # at the signup time of day
        assert min(delays) < timedelta(days=60)
        assert max(delays) > timedelta(days=170)

    def test_deleted_customer_is_never_deleted_twice(self) -> None:
        customers = _customers(CUSTOMERS)

        deletions = _deletions(customers, DAYS)

        assert deletions
        assert max(Counter(customer_id for customer_id, _ in deletions).values()) == 1

    def test_no_deletion_within_thirty_days_of_signup(self) -> None:
        customers = _customers(CUSTOMERS)

        deletions = _deletions(customers, 30)

        assert deletions == []

    def test_update_sets_deleted_at_and_updated_at_to_the_same_instant(self) -> None:
        changes = DayChanges()
        [(customer_id, deleted_at)] = _deletions(_customers(CUSTOMERS), DAYS)[:1]
        customer = make_customer(customer_id).model_copy(update={"created_at": CREATED_AT})

        CustomerDeletionSimulator(DeterministicRandom(SIMULATION_SEED)).simulate_day(
            make_snapshot(deleted_at.date(), customers=[customer]), changes
        )

        [update] = changes.updates
        assert (update.record_type, update.key) == (Customer, {"id": customer_id})
        assert update.values == {"deleted_at": deleted_at, "updated_at": deleted_at}
        assert changes.insert_counts() == {}


def _customers(count: int) -> list[Customer]:
    return [make_customer(i).model_copy(update={"created_at": CREATED_AT}) for i in range(1, count + 1)]


def _deletions(customers: list[Customer], days: int) -> list[tuple[int, datetime]]:
    """Every deletion over `days` days of a shop whose customers never leave the snapshot."""
    simulator = CustomerDeletionSimulator(DeterministicRandom(SIMULATION_SEED))
    snapshot = make_snapshot(SIGNUP_DAY, customers=customers)
    deletions: list[tuple[int, datetime]] = []
    for offset in range(days):
        changes = DayChanges()
        simulator.simulate_day(snapshot.model_copy(update={"day": SIGNUP_DAY + timedelta(days=offset)}), changes)
        for update in changes.updates:
            deleted_at = update.values["deleted_at"]
            assert isinstance(deleted_at, datetime)
            deletions.append((int(str(update.key["id"])), deleted_at))
    return deletions
