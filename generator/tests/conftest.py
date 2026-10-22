from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import psycopg
import pytest
from helpers import (
    AFTER_SALES_DAYS,
    AFTER_SALES_SCALE,
    AFTER_SALES_START,
    FULL_YEAR_DAYS,
    FULL_YEAR_SCALE,
    FULL_YEAR_SEED,
    FULL_YEAR_TODAY,
    RUN_DAYS,
    RUN_SCALE,
    RUN_SEED,
    RUN_START,
    RUN_TODAY,
    SIMULATION_SEED,
    TRAFFIC_MONTH_DAYS,
    TRAFFIC_MONTH_SCALE,
    TRAFFIC_MONTH_START,
    fingerprint,
)
from psycopg import sql
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.behaviour.crm_activity import CrmActivitySimulator
from sherpa_generator.behaviour.order_lifecycle import OrderLifecycleSimulator
from sherpa_generator.behaviour.visitor_traffic import VisitorTrafficSimulator
from sherpa_generator.database.connection import Database
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.database.record_writer import RecordWriter
from sherpa_generator.records import PendingEvent, TrackedEvent
from sherpa_generator.settings import GeneratorSettings
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules
from sherpa_generator.simulation.simulator import Simulator
from sherpa_generator.simulation.store_snapshot import StoreSnapshotLoader

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date, datetime


@pytest.fixture
def temp_database() -> Iterator[Database]:
    """A throwaway database with the raw schema applied, dropped afterwards."""
    with temporary_database() as db:
        yield db


@pytest.fixture(scope="session")
def traffic_month() -> Iterator[Database]:
    """One shared 30-day traffic + CRM run at scale 0.2. Tests must not commit changes to it."""
    with temporary_database() as db:
        simulated_days(db, TRAFFIC_MONTH_DAYS, TRAFFIC_MONTH_SCALE, TRAFFIC_MONTH_START, through="traffic")
        yield db


@pytest.fixture(scope="session")
def order_month() -> Iterator[Database]:
    """The `traffic_month` run with orders placed and progressed. Tests must not commit changes to it."""
    with temporary_database() as db:
        simulated_days(db, TRAFFIC_MONTH_DAYS, TRAFFIC_MONTH_SCALE, TRAFFIC_MONTH_START, through="orders")
        yield db


@pytest.fixture(scope="session")
def after_sales_season() -> Iterator[Database]:
    """Every behaviour for long enough that returns get through to receipt. Tests must not commit changes to it."""
    with temporary_database() as db:
        simulated_days(db, AFTER_SALES_DAYS, AFTER_SALES_SCALE, AFTER_SALES_START, through="all")
        yield db


@pytest.fixture(scope="session")
def simulator_reference() -> dict[str, object]:
    """Fingerprint of one uninterrupted Simulator run that the determinism tests must reproduce."""
    with temporary_database() as db:
        Simulator(db, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY).run(RUN_DAYS, RUN_START)
        return fingerprint(db)


@pytest.fixture(scope="module")
def full_year() -> Iterator[tuple[Database, float]]:
    """One Simulator run of a whole year at scale 1.0 and its runtime in seconds. Tests must not commit changes."""
    with temporary_database() as db:
        started = time.perf_counter()
        Simulator(db, seed=FULL_YEAR_SEED, scale=FULL_YEAR_SCALE, today=FULL_YEAR_TODAY).run(FULL_YEAR_DAYS)
        yield db, time.perf_counter() - started


@contextmanager
def temporary_database() -> Iterator[Database]:
    settings = GeneratorSettings()
    name = f"sherpa_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

    try:
        db = Database(settings, dbname=name)
        RawSchema(db).create()
        yield db
    finally:
        # Not WITH (FORCE): on Postgres 16 that refuses to terminate an autovacuum worker unless the role has
        # pg_signal_backend, while a plain DROP stops autovacuum itself (and still fails on a leaked connection).
        with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def simulated_days(
    db: Database, days: int, scale: float, start: date, through: Literal["traffic", "orders", "all"]
) -> None:
    """Setup plus the simulators up to `through`, one transaction per day, ending the day before `today`.

    `all` is the Simulator itself; the partial runs leave out catalog drift and the steps after `through`.
    """
    if through == "all":
        Simulator(db, seed=SIMULATION_SEED, scale=scale, today=start + timedelta(days=days)).run(days, start)
        return

    random = DeterministicRandom(SIMULATION_SEED)
    keys = PrimaryKeyAllocator({})
    rules, calendar = RealismRules(), SimulationCalendar()
    with db.transaction() as conn:
        changes = DayChanges()
        CatalogBuilder(random, keys).build(changes, start, start + timedelta(days=days - 1))
        RecordWriter(conn).write(changes)

    traffic = VisitorTrafficSimulator(random, keys, rules, calendar, scale)
    crm = CrmActivitySimulator(random, keys, rules, scale)
    orders = OrderLifecycleSimulator(random, keys, rules, calendar)
    for offset in range(days):
        day = start + timedelta(days=offset)
        with db.transaction() as conn:
            snapshot = StoreSnapshotLoader(conn, random).load(day)
            changes = DayChanges()
            _flush_pending_events(conn, changes, calendar.day_bounds(day))
            outcome = traffic.simulate_day(snapshot, changes)
            crm.simulate_day(snapshot, changes)
            if through == "orders":
                orders.place_orders(snapshot, changes, outcome.purchase_intents)
                orders.progress_orders(snapshot, changes)
            RecordWriter(conn).write(changes)


def _flush_pending_events(conn: psycopg.Connection, changes: DayChanges, bounds: tuple[datetime, datetime]) -> None:
    columns = ", ".join(PendingEvent.columns())
    rows = conn.execute(
        f"SELECT {columns} FROM {PendingEvent.TABLE} WHERE received_at >= %s AND received_at < %s",
        list(bounds),
    ).fetchall()
    for row in rows:
        changes.insert(TrackedEvent.model_validate(dict(zip(PendingEvent.columns(), row, strict=True))))

    changes.remove_pending(row[0] for row in rows)
