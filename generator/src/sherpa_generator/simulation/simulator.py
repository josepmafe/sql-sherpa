from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sherpa_generator.behaviour.ad_spend import AdSpendSimulator
from sherpa_generator.behaviour.after_sales import AfterSalesSimulator
from sherpa_generator.behaviour.catalog_drift import CatalogDriftSimulator
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.behaviour.crm_activity import CrmActivitySimulator
from sherpa_generator.behaviour.customer_deletions import CustomerDeletionSimulator
from sherpa_generator.behaviour.order_lifecycle import OrderLifecycleSimulator
from sherpa_generator.behaviour.visitor_traffic import VisitorTrafficSimulator
from sherpa_generator.database.record_writer import RecordWriter
from sherpa_generator.records import PendingEvent, TrackedEvent
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules
from sherpa_generator.simulation.simulation_state import SimulationState, SimulationStateStore
from sherpa_generator.simulation.store_snapshot import StoreSnapshotLoader

if TYPE_CHECKING:
    from datetime import date

    import psycopg

    from sherpa_generator.database.connection import Database

logger = logging.getLogger(__name__)

_ONE_DAY = timedelta(days=1)


class GeneratorError(Exception):
    """A run the generator refuses; nothing has been written."""


@dataclass(frozen=True, slots=True)
class RunResult:
    first_day: date | None
    last_day: date | None
    rows_written: dict[str, int]


class Simulator:
    """Spec A §5.1: simulates whole days up to yesterday, one transaction per day, resumable from the stored state."""

    def __init__(self, db: Database, *, seed: int, scale: float, today: date) -> None:
        self._db = db
        self._seed = seed
        self._scale = scale
        self._yesterday = today - _ONE_DAY
        self._random = DeterministicRandom(seed)
        self._rules = RealismRules()
        self._calendar = SimulationCalendar()

    def run(self, days: int, start_date: date | None = None) -> RunResult:
        if days < 1:
            msg = f"days must be at least 1, got {days}"
            raise GeneratorError(msg)

        with self._db.connect() as conn:
            state = SimulationStateStore(conn).load()

        rows: Counter[str] = Counter()
        if state is None:
            state, rows = self._set_up(start_date or self._yesterday - timedelta(days=days - 1))
        else:
            self._check_resumable(state, start_date)

        first = state.start_date if state.last_simulated_date is None else state.last_simulated_date + _ONE_DAY
        last = min(first + timedelta(days=days - 1), self._yesterday)
        if first > last:
            logger.info("nothing to simulate: already at %s", state.last_simulated_date)
            return RunResult(first_day=None, last_day=None, rows_written={})

        # Key-allocating simulators are wired per run, from the stored counters, so a run after a crash in the same
        # process continues from the committed keys rather than the rolled-back ones.
        keys = PrimaryKeyAllocator(state.next_ids)
        day_simulator = _DaySimulator(self._random, keys, self._rules, self._calendar, self._scale)
        day = first
        while day <= last:
            with self._db.transaction() as conn:
                changes = day_simulator.simulate(conn, day)
                written = RecordWriter(conn).write(changes)
                state = state.model_copy(update={"last_simulated_date": day, "next_ids": keys.next_ids()})
                SimulationStateStore(conn).save(state)

            rows.update(written)
            logger.info("simulated %s: %d rows", day, sum(written.values()))
            day += _ONE_DAY

        return RunResult(first_day=first, last_day=last, rows_written=dict(rows))

    def _set_up(self, start_date: date) -> tuple[SimulationState, Counter[str]]:
        """Catalog and initial state in one transaction. The catalog spans up to yesterday, whatever `days` is, so a
        short first run followed by resumes builds the same catalog as one long run."""
        if start_date > self._yesterday:
            msg = f"start date {start_date} is after yesterday ({self._yesterday})"
            raise GeneratorError(msg)

        keys = PrimaryKeyAllocator({})
        changes = DayChanges()
        CatalogBuilder(self._random, keys).build(changes, start_date, self._yesterday)
        state = SimulationState(
            seed=self._seed,
            scale=self._scale,
            start_date=start_date,
            last_simulated_date=None,
            next_ids=keys.next_ids(),
        )
        with self._db.transaction() as conn:
            written = RecordWriter(conn).write(changes)
            SimulationStateStore(conn).save(state)

        logger.info("set up the catalog from %s: %d rows", start_date, sum(written.values()))
        return state, Counter(written)

    def _check_resumable(self, state: SimulationState, start_date: date | None) -> None:
        if (state.seed, state.scale) != (self._seed, self._scale):
            msg = (
                f"stored state has seed {state.seed}, scale {state.scale}; got seed {self._seed}, scale {self._scale}"
                " (reset to start over)"
            )
            raise GeneratorError(msg)

        if start_date is not None:
            msg = f"a start date can only be given on the first run; the simulation started on {state.start_date}"
            raise GeneratorError(msg)


class _DaySimulator:
    """Spec A §5.2: the behaviour simulators in their daily order, all sharing one run's key allocator."""

    def __init__(
        self,
        random: DeterministicRandom,
        keys: PrimaryKeyAllocator,
        rules: RealismRules,
        calendar: SimulationCalendar,
        scale: float,
    ) -> None:
        self._random = random
        self._calendar = calendar
        self._drift = CatalogDriftSimulator(random, rules)
        self._traffic = VisitorTrafficSimulator(random, keys, rules, calendar, scale)
        self._crm = CrmActivitySimulator(random, keys, rules, scale)
        self._orders = OrderLifecycleSimulator(random, keys, rules, calendar)
        self._after_sales = AfterSalesSimulator(random, keys, calendar)
        self._ad_spend = AdSpendSimulator(random, rules)
        self._deletions = CustomerDeletionSimulator(random)

    def simulate(self, conn: psycopg.Connection, day: date) -> DayChanges:
        snapshot = StoreSnapshotLoader(conn, self._random).load(day)
        changes = DayChanges()
        self._flush_pending_events(conn, changes, day)
        self._drift.simulate_day(snapshot, changes)
        traffic = self._traffic.simulate_day(snapshot, changes)
        self._crm.simulate_day(snapshot, changes)
        self._orders.place_orders(snapshot, changes, traffic.purchase_intents)
        self._orders.progress_orders(snapshot, changes)
        self._after_sales.simulate_day(snapshot, changes)
        self._ad_spend.simulate_day(snapshot, changes, traffic.paid_sessions_by_campaign)
        self._deletions.simulate_day(snapshot, changes)
        return changes

    def _flush_pending_events(self, conn: psycopg.Connection, changes: DayChanges, day: date) -> None:
        """R2: late events land in the batch of the day that contains their `received_at`."""
        columns = PendingEvent.columns()
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {PendingEvent.TABLE} WHERE received_at >= %s AND received_at < %s",  # type: ignore[arg-type]
            list(self._calendar.day_bounds(day)),
        ).fetchall()
        for row in rows:
            changes.insert(TrackedEvent.model_validate(dict(zip(columns, row, strict=True))))

        changes.remove_pending(row[0] for row in rows)
