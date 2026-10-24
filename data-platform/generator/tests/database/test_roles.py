from __future__ import annotations

import os

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration


def test_agent_sessions_are_read_only_with_timeout() -> None:
    with _connect("AGENT") as conn:
        read_only = conn.execute("SHOW default_transaction_read_only").fetchone()
        timeout = conn.execute("SHOW statement_timeout").fetchone()

    assert read_only == ("on",)
    assert timeout == ("5s",)


def test_agent_cannot_create_objects() -> None:
    with _connect("AGENT") as conn:
        with pytest.raises((errors.ReadOnlySqlTransaction, errors.InsufficientPrivilege)):
            conn.execute("CREATE TABLE public.x(i int)")

        conn.rollback()
        exists = conn.execute("SELECT count(*) FROM pg_tables WHERE tablename = 'x'").fetchone()

    assert exists == (0,)


def test_agent_cannot_escape_read_only_by_default_in_new_session() -> None:
    with _connect("AGENT") as first:
        first.execute("SET default_transaction_read_only = off")
        first.commit()

    with _connect("AGENT") as second:
        read_only = second.execute("SHOW default_transaction_read_only").fetchone()

    assert read_only == ("on",)


def test_admin_owns_database_and_can_create_databases() -> None:
    with _connect("ADMIN") as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        can_create_db = conn.execute("SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user").fetchone()

    assert owner == ("sherpa_admin",)
    assert can_create_db == (True,)


def _connect(role: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["SHERPA_DB_HOST"],
        port=os.environ["SHERPA_DB_PORT"],
        dbname=os.environ["SHERPA_DB_NAME"],
        user=os.environ[f"SHERPA_{role}_USER"],
        password=os.environ[f"SHERPA_{role}_PASSWORD"],
    )
