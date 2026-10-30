from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from helpers import all_record_types
from pydantic import BaseModel, ValidationError
from sherpa_generator.records import EventContext, EventProperties, Order, PendingEvent, TrackedEvent
from sherpa_generator.records.base import Money

NOW = datetime(2025, 7, 1, 14, 3, 22, tzinfo=UTC)


def test_every_record_type_has_a_table_and_unique_columns() -> None:
    record_types = all_record_types()

    assert len(record_types) == 22
    for record_type in record_types:
        columns = record_type.columns()
        assert record_type.TABLE.count(".") == 1, record_type
        assert len(columns) == len(set(columns)), record_type
        assert set(record_type.KEY) <= set(columns), record_type


def test_every_record_type_has_a_distinct_table() -> None:
    tables = [t.TABLE for t in all_record_types()]

    assert len(tables) == len(set(tables))


def test_money_quantizes_half_up() -> None:
    class Priced(BaseModel):
        amount: Money

    assert Priced(amount=Decimal("1.005")).amount == Decimal("1.01")
    assert Priced(amount=Decimal("1.004")).amount == Decimal("1.00")
    assert Priced(amount=Decimal("-1.005")).amount == Decimal("-1.01")


def test_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Order(**_order_kwargs(), surprise=1)


@pytest.mark.parametrize("occurred_at", ["2025-07-01T14:03:22", "2025-07-01 14:03:22+02:00", "yesterday", ""])
def test_tracked_event_rejects_occurred_at_without_offset(occurred_at: str) -> None:
    with pytest.raises(ValidationError):
        _event(occurred_at=occurred_at)


@pytest.mark.parametrize(
    "occurred_at", ["2025-07-01T14:03:22+02:00", "2025-07-01T14:03:22.123Z", "2025-07-01T14:03:22-05:30"]
)
def test_tracked_event_accepts_iso_8601_with_offset(occurred_at: str) -> None:
    assert _event(occurred_at=occurred_at).occurred_at == occurred_at


def test_copy_values_serializes_enums_and_nested_payloads() -> None:
    order = Order(**_order_kwargs())
    event = _event(
        context=EventContext(device_type="mobile", utm_source="google"),
        properties=EventProperties(cart_value=Decimal("19.999")),
    )

    order_values = dict(zip(Order.columns(), order.copy_values(), strict=True))
    event_values = dict(zip(TrackedEvent.columns(), event.copy_values(), strict=True))

    assert order_values["status"] == "paid"
    assert type(order_values["status"]) is str
    assert event_values["context"] == {"device_type": "mobile", "utm_source": "google"}
    assert event_values["properties"] == {"cart_value": "20.00"}


def test_pending_event_shares_columns_with_events_but_not_table() -> None:
    assert PendingEvent.columns() == TrackedEvent.columns()
    assert PendingEvent.TABLE == "raw_meta.pending_events"
    assert TrackedEvent.TABLE == "raw_events.events"


def _order_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "id": 1,
        "order_number": "SS-000001",
        "customer_id": 7,
        "shipping_address_id": 9,
        "status": "paid",
        "subtotal": Decimal("100.00"),
        "discount_total": Decimal("0.00"),
        "shipping_amount": Decimal("4.95"),
        "tax_amount": Decimal("20.00"),
        "order_total": Decimal("124.95"),
        "sales_channel": "web",
        "placed_at": NOW,
        "updated_at": NOW,
    }
    kwargs.update(overrides)
    return kwargs


def _event(**overrides: Any) -> TrackedEvent:
    kwargs: dict[str, Any] = {
        "event_id": "6f1c1c58-0000-4000-8000-000000000001",
        "anonymous_id": "anon-1",
        "user_id": None,
        "event_type": "page_view",
        "occurred_at": "2025-07-01T14:03:22+00:00",
        "received_at": NOW,
        "context": EventContext(device_type="desktop"),
        "properties": EventProperties(),
    }
    kwargs.update(overrides)
    return TrackedEvent(**kwargs)
