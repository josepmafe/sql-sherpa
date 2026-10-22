from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sherpa_generator.records import Customer, Order, OrderStatus, RawRecord, SalesChannel

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def all_record_types() -> list[type[RawRecord]]:
    return _subclasses(RawRecord)


def make_customer(customer_id: int) -> Customer:
    return Customer(
        id=customer_id,
        email=f"c{customer_id}@example.com",
        first_name="A",
        last_name="B",
        country_code="DE",
        marketing_opt_in=True,
        is_test_account=False,
        created_at=NOW,
        updated_at=NOW,
    )


def make_order(order_id: int) -> Order:
    return Order(
        id=order_id,
        order_number=f"SS-{order_id:06d}",
        customer_id=1,
        shipping_address_id=1,
        status=OrderStatus.PLACED,
        subtotal=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        shipping_amount=Decimal("4.95"),
        tax_amount=Decimal("2.00"),
        order_total=Decimal("16.95"),
        sales_channel=SalesChannel.WEB,
        placed_at=NOW,
        updated_at=NOW,
    )


def _subclasses(cls: type[RawRecord]) -> list[type[RawRecord]]:
    found: list[type[RawRecord]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found
