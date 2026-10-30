from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

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
    MONTH_DAYS,
    MONTH_END,
    MONTH_SCALE,
    MONTH_START,
    RUN_DAYS,
    RUN_SCALE,
    RUN_SEED,
    RUN_START,
    RUN_TODAY,
    SIMULATION_SEED,
    fingerprint,
)
from psycopg import sql
from sherpa_generator.database.connection import Database
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.settings import GeneratorSettings
from sherpa_generator.simulation.simulator import Simulator

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def temp_database() -> Iterator[Database]:
    """A throwaway database with the raw schema applied, dropped afterwards."""
    with temporary_database() as db:
        yield db


@pytest.fixture(scope="session")
def month() -> Iterator[Database]:
    """One Simulator run of 30 days at scale 0.2 (spec §5.4). Read-only: the teardown fails if a test wrote to it."""
    with temporary_database() as db:
        Simulator(db, seed=RUN_SEED, scale=MONTH_SCALE, today=MONTH_END).run(MONTH_DAYS, MONTH_START)
        before = fingerprint(db)
        yield db
        assert fingerprint(db) == before, "a test wrote into the shared `month` run"


@pytest.fixture(scope="session")
def after_sales_season() -> Iterator[Database]:
    """Every behaviour for long enough that returns get through to receipt. Tests must not commit changes to it."""
    with temporary_database() as db:
        today = AFTER_SALES_START + timedelta(days=AFTER_SALES_DAYS)
        Simulator(db, seed=SIMULATION_SEED, scale=AFTER_SALES_SCALE, today=today).run(
            AFTER_SALES_DAYS, AFTER_SALES_START
        )
        before = fingerprint(db)
        yield db
        assert fingerprint(db) == before, "a test wrote into the shared `after_sales_season` run"


@pytest.fixture(scope="session")
def simulator_reference() -> Iterator[dict[str, object]]:
    """Fingerprint of one uninterrupted Simulator run that the determinism tests must reproduce."""
    with temporary_database() as db:
        Simulator(db, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY).run(RUN_DAYS, RUN_START)
        reference = fingerprint(db)
        yield reference
        assert fingerprint(db) == reference, "a test wrote into the shared `simulator_reference` run"


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
