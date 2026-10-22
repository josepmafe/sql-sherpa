from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg
import pytest
from helpers import NOW, make_customer, make_order
from sherpa_generator.database.record_writer import RecordWriter
from sherpa_generator.records import (
    AdSpendDay,
    Customer,
    DeviceType,
    EventContext,
    EventProperties,
    EventType,
    Order,
    PendingEvent,
    TrackedEvent,
)
from sherpa_generator.simulation.day_changes import DayChanges

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

pytestmark = pytest.mark.integration


def make_event(event_id: str, cls: type[TrackedEvent] = TrackedEvent) -> TrackedEvent:
    return cls(
        event_id=event_id,
        anonymous_id="anon-1",
        event_type=EventType.PURCHASE,
        occurred_at="2025-01-01T13:00:00+01:00",
        received_at=NOW,
        context=EventContext(device_type=DeviceType.MOBILE, utm_source="google"),
        properties=EventProperties(order_id=7, cart_value=Decimal("20.00")),
    )


class TestRecordWriter:
    def test_write_round_trips_records_including_jsonb_and_enums(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.insert(make_customer(1))
        changes.insert(make_event("e1"))

        with temp_database.transaction() as conn:
            counts = RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            customer_row = conn.execute("SELECT * FROM raw_shop.customers").fetchone()
            event_row = conn.execute("SELECT * FROM raw_events.events").fetchone()
        assert counts == {Customer.TABLE: 1, TrackedEvent.TABLE: 1}
        assert customer_row is not None
        assert Customer(**dict(zip(Customer.columns(), customer_row, strict=True))) == make_customer(1)
        assert event_row is not None
        assert TrackedEvent(**dict(zip(TrackedEvent.columns(), event_row, strict=True))) == make_event("e1")

    def test_write_stores_nested_payload_as_jsonb_without_nulls(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.insert(make_event("e1"))

        with temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            row = conn.execute(
                "SELECT context->>'device_type', context ? 'referrer', properties->>'order_id' FROM raw_events.events"
            ).fetchone()
        assert row == ("mobile", False, "7")

    def test_write_routes_pending_events_to_their_own_table(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.insert(make_event("p1", PendingEvent))

        with temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            pending = conn.execute("SELECT count(*) FROM raw_meta.pending_events").fetchone()
            events = conn.execute("SELECT count(*) FROM raw_events.events").fetchone()
        assert (pending, events) == ((1,), (0,))

    def test_write_applies_updates_after_inserts_in_same_batch(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.update(Order, 1, status="paid")
        changes.insert(make_order(1))
        changes.insert(make_order(2))
        changes.update(Order, 1, status="shipped", updated_at=datetime(2025, 1, 3, tzinfo=UTC))

        with temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            rows = conn.execute("SELECT id, status, updated_at FROM raw_shop.orders ORDER BY id").fetchall()
        assert rows == [(1, "shipped", datetime(2025, 1, 3, tzinfo=UTC)), (2, "placed", NOW)]

    def test_write_update_with_tuple_key_matches_composite_key(self, temp_database: Database) -> None:
        spend = AdSpendDay(spend_date=NOW.date(), campaign_id=1, spend=Decimal("5.00"), impressions=100, clicks=10)
        changes = DayChanges()
        changes.insert(spend)
        changes.insert(spend.model_copy(update={"campaign_id": 2}))
        changes.update(AdSpendDay, (NOW.date(), 2), clicks=99)

        with temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            rows = conn.execute("SELECT campaign_id, clicks FROM raw_marketing.ad_spend_daily ORDER BY 1").fetchall()
        assert rows == [(1, 10), (2, 99)]

    def test_write_deletes_pending_events_after_inserts(self, temp_database: Database) -> None:
        seed = DayChanges()
        seed.insert(make_event("p1", PendingEvent))
        seed.insert(make_event("p2", PendingEvent))
        with temp_database.transaction() as conn:
            RecordWriter(conn).write(seed)
        changes = DayChanges()
        changes.insert(make_event("p1"))
        changes.remove_pending(["p1"])

        with temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            remaining = conn.execute("SELECT event_id FROM raw_meta.pending_events").fetchall()
            flushed = conn.execute("SELECT event_id FROM raw_events.events").fetchall()
        assert (remaining, flushed) == ([("p2",)], [("p1",)])

    def test_write_with_no_changes_writes_nothing(self, temp_database: Database) -> None:
        with temp_database.transaction() as conn:
            counts = RecordWriter(conn).write(DayChanges())

        assert counts == {}

    def test_write_does_not_commit(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.insert(make_customer(1))

        with temp_database.connect() as conn:
            RecordWriter(conn).write(changes)
            conn.rollback()
            row = conn.execute("SELECT count(*) FROM raw_shop.customers").fetchone()

        assert row == (0,)

    def test_write_duplicate_primary_key_fails_and_leaves_nothing(self, temp_database: Database) -> None:
        changes = DayChanges()
        changes.insert(make_customer(1))
        changes.insert(make_customer(1))

        with pytest.raises(psycopg.errors.UniqueViolation), temp_database.transaction() as conn:
            RecordWriter(conn).write(changes)

        with temp_database.connect() as conn:
            assert conn.execute("SELECT count(*) FROM raw_shop.customers").fetchone() == (0,)
