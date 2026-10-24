from __future__ import annotations

from sherpa_generator.records import Customer, Order
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator


class TestPrimaryKeyAllocator:
    def test_next_id_continues_from_saved_counters(self) -> None:
        allocator = PrimaryKeyAllocator({Order.TABLE: 10})

        ids = [allocator.next_id(Order), allocator.next_id(Order), allocator.next_id(Customer)]

        assert ids == [10, 11, 1]
        assert allocator.next_ids() == {Order.TABLE: 12, Customer.TABLE: 2}

    def test_next_ids_returns_a_copy(self) -> None:
        allocator = PrimaryKeyAllocator({})
        snapshot = allocator.next_ids()
        snapshot[Order.TABLE] = 99

        assert allocator.next_id(Order) == 1

    def test_constructor_does_not_alias_the_input_mapping(self) -> None:
        saved = {Order.TABLE: 5}
        allocator = PrimaryKeyAllocator(saved)

        allocator.next_id(Order)

        assert saved == {Order.TABLE: 5}
