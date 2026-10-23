from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import RUN_DAYS, RUN_SCALE, RUN_SEED, RUN_START, RUN_TODAY, STATE_TABLE, fingerprint
from sherpa_generator.behaviour.catalog_setup import ALWAYS_ON_CHANNELS
from sherpa_generator.behaviour.customer_deletions import CustomerDeletionSimulator
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.simulation.simulation_state import SimulationStateStore
from sherpa_generator.simulation.simulator import GeneratorError, RunResult, Simulator

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.simulation_state import SimulationState
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

pytestmark = pytest.mark.integration

YESTERDAY = RUN_TODAY - timedelta(days=1)
SMALL_SCALE = 0.05


class SimulatedCrashError(Exception):
    pass


class TestSimulator:
    def test_fresh_runs_are_identical(self, temp_database: Database, simulator_reference: dict[str, object]) -> None:
        make_simulator(temp_database).run(RUN_DAYS, RUN_START)

        assert fingerprint(temp_database) == simulator_reference

    def test_resume_equals_single_run(self, temp_database: Database, simulator_reference: dict[str, object]) -> None:
        simulator = make_simulator(temp_database)
        simulator.run(4, RUN_START)
        pending_after_four_days = row_count(temp_database, "raw_meta.pending_events")

        simulator.run(6)

        assert pending_after_four_days > 0  # late events cross the resume boundary
        assert fingerprint(temp_database) == simulator_reference

    def test_crash_mid_day_rolls_back_and_resume_matches(
        self, temp_database: Database, simulator_reference: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        crash_day = RUN_START + timedelta(days=2)
        simulator = make_simulator(temp_database)
        crash_deletions_on(monkeypatch, crash_day)

        with pytest.raises(SimulatedCrashError):
            simulator.run(RUN_DAYS, RUN_START)

        assert stored_state(temp_database).last_simulated_date == RUN_START + timedelta(days=1)
        assert latest_activity(temp_database) < datetime(2025, 3, 3, tzinfo=UTC)
        monkeypatch.undo()
        simulator.run(RUN_DAYS - 2)
        assert fingerprint(temp_database) == simulator_reference

    def test_crash_on_first_day_keeps_setup_and_resumes_from_start(
        self, temp_database: Database, simulator_reference: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator = make_simulator(temp_database)
        crash_deletions_on(monkeypatch, RUN_START)

        with pytest.raises(SimulatedCrashError):
            simulator.run(RUN_DAYS, RUN_START)

        state = stored_state(temp_database)
        assert (state.start_date, state.last_simulated_date) == (RUN_START, None)
        assert row_count(temp_database, "raw_shop.products") > 0
        assert row_count(temp_database, "raw_shop.orders") == 0
        monkeypatch.undo()
        simulator.run(RUN_DAYS)
        assert fingerprint(temp_database) == simulator_reference

    def test_run_reports_range_and_rows_written(self, temp_database: Database) -> None:
        result = make_simulator(temp_database).run(2, date(2025, 3, 10))

        assert (result.first_day, result.last_day) == (date(2025, 3, 10), date(2025, 3, 11))
        assert result.rows_written["raw_shop.orders"] == row_count(temp_database, "raw_shop.orders")
        assert result.rows_written["raw_shop.products"] == row_count(temp_database, "raw_shop.products")

    def test_first_run_defaults_to_ending_yesterday(self, temp_database: Database) -> None:
        result = make_simulator(temp_database).run(2)

        assert (result.first_day, result.last_day) == (date(2025, 3, 30), YESTERDAY)
        assert stored_state(temp_database).start_date == date(2025, 3, 30)

    def test_start_date_yesterday_simulates_one_day(self, temp_database: Database) -> None:
        result = make_simulator(temp_database).run(5, YESTERDAY)

        assert (result.first_day, result.last_day) == (YESTERDAY, YESTERDAY)

    def test_run_when_caught_up_is_noop(self, temp_database: Database) -> None:
        simulator = make_simulator(temp_database)
        simulator.run(3, date(2025, 3, 29))
        before = fingerprint(temp_database)

        result = simulator.run(5)

        assert result == RunResult(first_day=None, last_day=None, rows_written={})
        assert fingerprint(temp_database) == before

    def test_resume_stops_at_yesterday(self, temp_database: Database) -> None:
        make_simulator(temp_database).run(2, date(2025, 3, 29))

        result = make_simulator(temp_database).run(5)

        assert (result.first_day, result.last_day) == (YESTERDAY, YESTERDAY)
        assert stored_state(temp_database).last_simulated_date == YESTERDAY

    def test_never_beyond_yesterday(self, temp_database: Database) -> None:
        result = make_simulator(temp_database).run(400, date(2025, 3, 25))

        assert (result.first_day, result.last_day) == (date(2025, 3, 25), date(2025, 3, 31))
        assert stored_state(temp_database).last_simulated_date == date(2025, 3, 31)
        assert latest_activity(temp_database) < datetime(2025, 4, 1, tzinfo=UTC)

    def test_zero_days_rejected(self, temp_database: Database) -> None:
        with pytest.raises(GeneratorError, match="days must be at least 1, got 0"):
            make_simulator(temp_database).run(0, RUN_START)

        assert_empty(temp_database)

    def test_start_date_after_yesterday_rejected(self, temp_database: Database) -> None:
        with pytest.raises(GeneratorError, match="start date 2025-04-01 is after yesterday \\(2025-03-31\\)"):
            make_simulator(temp_database).run(1, RUN_TODAY)

        assert_empty(temp_database)

    def test_seed_mismatch_rejected(self, temp_database: Database) -> None:
        make_simulator(temp_database).run(1, RUN_START)
        before = fingerprint(temp_database)

        with pytest.raises(
            GeneratorError, match=re.escape("stored state has seed 7, scale 0.1; got seed 8, scale 0.1")
        ):
            Simulator(temp_database, seed=8, scale=RUN_SCALE, today=RUN_TODAY).run(1)

        assert fingerprint(temp_database) == before

    def test_scale_mismatch_rejected(self, temp_database: Database) -> None:
        make_simulator(temp_database).run(1, RUN_START)
        before = fingerprint(temp_database)

        with pytest.raises(
            GeneratorError, match=re.escape("stored state has seed 7, scale 0.1; got seed 7, scale 0.2")
        ):
            Simulator(temp_database, seed=RUN_SEED, scale=0.2, today=RUN_TODAY).run(1)

        assert fingerprint(temp_database) == before

    def test_scale_that_loses_float_precision_in_storage_resumes(self, temp_database: Database) -> None:
        # 0.1 * 3 is 0.30000000000000004; the numeric column stores it as 0.3.
        scale = 0.1 * 3
        Simulator(temp_database, seed=RUN_SEED, scale=scale, today=RUN_TODAY).run(1, RUN_START)

        Simulator(temp_database, seed=RUN_SEED, scale=scale, today=RUN_TODAY).run(1)

        assert stored_state(temp_database).last_simulated_date == RUN_START + timedelta(days=1)

    def test_start_date_with_existing_state_rejected(self, temp_database: Database) -> None:
        make_simulator(temp_database).run(1, RUN_START)
        before = fingerprint(temp_database)

        with pytest.raises(GeneratorError, match="a start date can only be given on the first run"):
            make_simulator(temp_database).run(1, date(2025, 3, 2))

        assert fingerprint(temp_database) == before

    def test_catalog_is_independent_of_the_day_of_the_first_run(self, temp_database: Database) -> None:
        Simulator(temp_database, seed=RUN_SEED, scale=SMALL_SCALE, today=date(2026, 6, 1)).run(2, RUN_START)
        first = catalog_rows(temp_database)
        RawSchema(temp_database).drop()
        RawSchema(temp_database).create()

        Simulator(temp_database, seed=RUN_SEED, scale=SMALL_SCALE, today=date(2027, 9, 15)).run(2, RUN_START)

        assert catalog_rows(temp_database) == first

    def test_catalog_ends_at_yesterday_inside_the_first_year(self, temp_database: Database) -> None:
        Simulator(temp_database, seed=RUN_SEED, scale=SMALL_SCALE, today=RUN_TODAY).run(2, RUN_START)

        ends = always_on_campaign_ends(temp_database)

        # ends_at is exclusive: the always-on campaigns cover yesterday (2025-03-31) whole.
        assert ends == {datetime(2025, 4, 1, tzinfo=UTC)}

    def test_catalog_ends_one_year_after_the_start_date(self, temp_database: Database) -> None:
        Simulator(temp_database, seed=RUN_SEED, scale=SMALL_SCALE, today=date(2027, 9, 15)).run(2, RUN_START)

        ends = always_on_campaign_ends(temp_database)

        # 365 days from 2025-03-01: the last covered day is 2026-02-28.
        assert ends == {datetime(2026, 3, 1, tzinfo=UTC)}


def make_simulator(db: Database) -> Simulator:
    return Simulator(db, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY)


def crash_deletions_on(monkeypatch: pytest.MonkeyPatch, crash_day: date) -> None:
    """Customer deletions run last in the day, so the crash hits after every other step has queued its changes."""
    original = CustomerDeletionSimulator.simulate_day

    def simulate_day(self: CustomerDeletionSimulator, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        if snapshot.day == crash_day:
            raise SimulatedCrashError

        original(self, snapshot, changes)

    monkeypatch.setattr(CustomerDeletionSimulator, "simulate_day", simulate_day)


def catalog_rows(db: Database) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with db.connect() as conn:
        campaigns = conn.execute("SELECT * FROM raw_marketing.campaigns ORDER BY id").fetchall()
        promotions = conn.execute("SELECT * FROM raw_shop.promotions ORDER BY id").fetchall()

    assert campaigns
    assert promotions
    return campaigns, promotions


def always_on_campaign_ends(db: Database) -> set[datetime]:
    with db.connect() as conn:
        rows = conn.execute("SELECT ends_at FROM raw_marketing.campaigns WHERE name LIKE 'Always On %'").fetchall()

    assert len(rows) == len(ALWAYS_ON_CHANNELS)
    return {row[0] for row in rows}


def stored_state(db: Database) -> SimulationState:
    with db.connect() as conn:
        state = SimulationStateStore(conn).load()

    assert state is not None
    return state


def row_count(db: Database, table: str) -> int:
    with db.connect() as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()

    assert row is not None
    return int(row[0])


def latest_activity(db: Database) -> datetime:
    """The latest timestamp a simulated day writes, across events, orders, CRM and ad spend."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT greatest("
            " (SELECT max(received_at) FROM raw_events.events),"
            " (SELECT max(updated_at) FROM raw_shop.orders),"
            " (SELECT max(updated_at) FROM raw_crm.leads),"
            " (SELECT max(spend_date)::timestamptz FROM raw_marketing.ad_spend_daily))"
        ).fetchone()

    assert row is not None
    return row[0]


def assert_empty(db: Database) -> None:
    tables = fingerprint(db)
    assert tables.pop(STATE_TABLE) is None
    assert all(count == 0 for count, _ in tables.values())
