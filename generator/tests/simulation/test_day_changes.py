from __future__ import annotations

from decimal import Decimal

import pytest
from helpers import make_customer, make_order
from sherpa_generator.records import Customer, Order, OrderStatus
from sherpa_generator.simulation.day_changes import DayChanges


class TestDayChanges:
    def test_update_rejects_unknown_column(self) -> None:
        changes = DayChanges()

        with pytest.raises(ValueError, match="nope"):
            changes.update(Order, 1, nope="x")

        assert changes.updates == []

    def test_update_rejects_invalid_value(self) -> None:
        changes = DayChanges()

        with pytest.raises(ValueError, match="status"):
            changes.update(Order, 1, status="shipping")

        assert changes.updates == []

    def test_update_rejects_empty_values(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            DayChanges().update(Order, 1)

    def test_update_rejects_key_that_does_not_match_the_record_key(self) -> None:
        with pytest.raises(ValueError, match="key"):
            DayChanges().update(Order, (1, 2), status="paid")

    def test_update_stores_validated_values(self) -> None:
        changes = DayChanges()

        changes.update(Order, 5, status="paid", tax_amount="2.505")

        [update] = changes.updates
        assert update.record_type is Order
        assert update.key == {"id": 5}
        assert update.values == {"status": OrderStatus.PAID, "tax_amount": Decimal("2.51")}

    def test_inserted_returns_records_of_one_type_in_insertion_order(self) -> None:
        changes = DayChanges()
        changes.insert(make_customer(2))
        changes.insert(make_order(1))
        changes.insert(make_customer(1))

        assert [c.id for c in changes.inserted(Customer)] == [2, 1]
        assert [o.id for o in changes.inserted(Order)] == [1]

    def test_inserted_is_empty_for_unseen_type(self) -> None:
        assert DayChanges().inserted(Order) == []

    def test_insert_counts_by_table(self) -> None:
        changes = DayChanges()
        changes.insert(make_customer(1))
        changes.insert(make_customer(2))
        changes.insert(make_order(1))

        assert changes.insert_counts() == {Customer.TABLE: 2, Order.TABLE: 1}

    def test_remove_pending_accumulates_ids(self) -> None:
        changes = DayChanges()

        changes.remove_pending(["a", "b"])
        changes.remove_pending(iter(["c"]))

        assert changes.pending_removals == ["a", "b", "c"]
