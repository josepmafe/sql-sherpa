from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from sherpa_generator.simulation.simulation_state import SimulationState, SimulationStateStore

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

pytestmark = pytest.mark.integration


def make_state(
    last_simulated_date: date | None = date(2025, 1, 30),
    next_ids: dict[str, int] | None = None,
) -> SimulationState:
    return SimulationState(
        seed=42,
        scale=0.2,
        start_date=date(2025, 1, 1),
        last_simulated_date=last_simulated_date,
        next_ids={"raw_shop.orders": 11, "raw_shop.customers": 4} if next_ids is None else next_ids,
    )


class TestSimulationStateStore:
    def test_load_returns_none_when_empty(self, temp_database: Database) -> None:
        with temp_database.connect() as conn:
            assert SimulationStateStore(conn).load() is None

    def test_save_then_load_round_trips(self, temp_database: Database) -> None:
        state = make_state()

        with temp_database.transaction() as conn:
            SimulationStateStore(conn).save(state)

        with temp_database.connect() as conn:
            assert SimulationStateStore(conn).load() == state

    def test_save_round_trips_state_before_first_day(self, temp_database: Database) -> None:
        state = make_state(last_simulated_date=None, next_ids={})

        with temp_database.transaction() as conn:
            SimulationStateStore(conn).save(state)

        with temp_database.connect() as conn:
            assert SimulationStateStore(conn).load() == state

    def test_save_overwrites_single_row(self, temp_database: Database) -> None:
        later = make_state(last_simulated_date=date(2025, 1, 31), next_ids={"raw_shop.orders": 20})

        with temp_database.transaction() as conn:
            store = SimulationStateStore(conn)
            store.save(make_state())
            store.save(later)

        with temp_database.connect() as conn:
            count = conn.execute("SELECT count(*) FROM raw_meta.simulation_state").fetchone()
            loaded = SimulationStateStore(conn).load()
        assert count == (1,)
        assert loaded == later

    def test_save_does_not_commit(self, temp_database: Database) -> None:
        with temp_database.connect() as conn:
            SimulationStateStore(conn).save(make_state())
            conn.rollback()

            assert SimulationStateStore(conn).load() is None
