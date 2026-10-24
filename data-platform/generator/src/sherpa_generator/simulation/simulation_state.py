from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    import psycopg


class SimulationState(BaseModel):
    """Spec A §5.3: what a resumed run needs to continue exactly where the last one stopped."""

    model_config = ConfigDict(frozen=True)

    seed: int
    scale: float
    start_date: date
    last_simulated_date: date | None
    next_ids: dict[str, int]


class SimulationStateStore:
    """Reads and writes the single raw_meta.simulation_state row; never commits."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def load(self) -> SimulationState | None:
        row = self._conn.execute(
            "SELECT seed, scale, start_date, last_simulated_date, next_ids FROM raw_meta.simulation_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        seed, scale, start_date, last_simulated_date, next_ids = row
        return SimulationState(
            seed=seed,
            scale=float(scale),
            start_date=start_date,
            last_simulated_date=last_simulated_date,
            next_ids=next_ids,
        )

    def save(self, state: SimulationState) -> None:
        self._conn.execute(
            "INSERT INTO raw_meta.simulation_state"
            " (id, seed, scale, start_date, last_simulated_date, next_ids, updated_at)"
            " VALUES (1, %s, %s, %s, %s, %s, now())"
            " ON CONFLICT (id) DO UPDATE SET seed = EXCLUDED.seed, scale = EXCLUDED.scale,"
            " start_date = EXCLUDED.start_date, last_simulated_date = EXCLUDED.last_simulated_date,"
            " next_ids = EXCLUDED.next_ids, updated_at = EXCLUDED.updated_at",
            [state.seed, state.scale, state.start_date, state.last_simulated_date, Jsonb(state.next_ids)],
        )
