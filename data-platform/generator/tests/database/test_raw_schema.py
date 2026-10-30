from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from helpers import all_record_types
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.records import PendingEvent, TrackedEvent

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

pytestmark = pytest.mark.integration

SIMULATION_STATE_COLUMNS = ("id", "seed", "scale", "start_date", "last_simulated_date", "next_ids", "updated_at")


class TestRawSchema:
    @pytest.mark.parametrize("drop_first", [False, True], ids=["create_again", "drop_then_create"])
    def test_create_leaves_the_same_tables(self, temp_database: Database, drop_first: bool) -> None:
        schema = RawSchema(temp_database)
        before = schema.tables()
        if drop_first:
            schema.drop()

        schema.create()

        assert schema.tables() == before

    def test_table_columns_match_records(self, temp_database: Database) -> None:
        tables = RawSchema(temp_database).tables()

        for record_type in all_record_types():
            assert tables[record_type.TABLE] == record_type.columns(), record_type.TABLE

    def test_simulation_state_columns(self, temp_database: Database) -> None:
        tables = RawSchema(temp_database).tables()

        assert tables["raw_meta.simulation_state"] == SIMULATION_STATE_COLUMNS

    def test_simulation_state_allows_only_row_one(self, temp_database: Database) -> None:
        with temp_database.connect() as conn:
            conn.execute("INSERT INTO raw_meta.simulation_state (id, seed, scale) VALUES (1, 42, 1.0)")

            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO raw_meta.simulation_state (id, seed, scale) VALUES (2, 42, 1.0)")

    def test_events_accept_duplicate_event_ids(self, temp_database: Database) -> None:
        for record_type in (TrackedEvent, PendingEvent):
            with temp_database.transaction() as conn:
                for _ in range(2):
                    conn.execute(
                        f"INSERT INTO {record_type.TABLE}"
                        " (event_id, anonymous_id, event_type, occurred_at, received_at)"
                        " VALUES ('dup', 'a', 'page_view', '2025-07-01T00:00:00+00:00', now())"
                    )

            with temp_database.connect() as conn:
                count = conn.execute(f"SELECT count(*) FROM {record_type.TABLE}").fetchone()
            assert count == (2,)

    def test_primary_keys_reject_duplicates(self, temp_database: Database) -> None:
        insert = "INSERT INTO raw_shop.categories (id, name) VALUES (1, 'Footwear')"

        with temp_database.connect() as conn:
            conn.execute(insert)

            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(insert)

    def test_drop_removes_all_raw_schemas(self, temp_database: Database) -> None:
        schema = RawSchema(temp_database)

        schema.drop()

        assert schema.tables() == {}
        with temp_database.connect() as conn:
            remaining = conn.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'raw\\_%'"
            )
            assert remaining.fetchall() == []
