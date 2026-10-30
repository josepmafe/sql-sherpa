"""What the roles script and the init revokes leave the agent and UI roles able to do, on the main database.

These need Postgres and `.env` only, no built relations. Reads of the built marts: `dbt/test_reader_access.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import errors
from psycopg.conninfo import make_conninfo

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import WarehouseSettings

pytestmark = pytest.mark.integration

# Each call is denied on EXECUTE before it runs, so the arguments only need the right types (spec §3).
LARGE_OBJECT_CALLS = [
    "lo_create(0)",
    "lo_creat(-1)",
    "lo_from_bytea(0, decode('00', 'hex'))",
    "lo_open(0, 262144)",
    "lowrite(0, decode('00', 'hex'))",
    "lo_put(0, 0, decode('00', 'hex'))",
]


@pytest.fixture(params=["agent", "ui"])
def role(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def reader(settings: WarehouseSettings, role: str) -> Iterator[psycopg.Connection]:
    # autocommit: a denied statement must not abort the session for the next one.
    with psycopg.connect(settings.conninfo("main", role), autocommit=True) as conn:  # type: ignore[arg-type]
        yield conn


class TestReaderSessions:
    def test_a_reader_cannot_create_temporary_tables_even_with_read_only_off(self, reader: psycopg.Connection) -> None:
        # The TEMPORARY revoke on the database; a temp table leaves nothing behind if it were allowed.
        reader.execute("SET default_transaction_read_only = off")

        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute("CREATE TEMP TABLE x(i int)")

    def test_a_reader_has_no_usage_or_create_on_public(self, reader: psycopg.Connection) -> None:
        row = reader.execute(
            "SELECT has_schema_privilege(current_user, 'public', 'USAGE'),"
            " has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone()

        assert row == (False, False)

    def test_readers_cannot_connect_to_the_postgres_database(self, settings: WarehouseSettings, role: str) -> None:
        conninfo = make_conninfo(settings.conninfo("main", role), dbname="postgres")  # type: ignore[arg-type]

        with pytest.raises(psycopg.OperationalError, match="User does not have CONNECT privilege"):
            psycopg.connect(conninfo)

    @pytest.mark.parametrize("call", LARGE_OBJECT_CALLS)
    def test_large_object_functions_are_revoked(self, reader: psycopg.Connection, call: str) -> None:
        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute(f"SELECT {call}")  # type: ignore[call-overload]


class TestRoleSettings:
    @pytest.mark.parametrize(
        ("login", "statement_timeout", "conn_limit"),
        [("agent_user", "5s", 20), ("ui_user", "30s", 10)],
    )
    def test_reader_role_settings(
        self,
        admin: psycopg.Connection,
        settings: WarehouseSettings,
        login: str,
        statement_timeout: str,
        conn_limit: int,
    ) -> None:
        role_name = getattr(settings, login)
        query = (
            "select s.setconfig, r.rolconnlimit from pg_roles r"
            " left join pg_db_role_setting s on s.setrole = r.oid and s.setdatabase = 0"
            " where r.rolname = %s"
        )

        row = admin.execute(query, (role_name,)).fetchone()

        assert row is not None
        assert sorted(row[0]) == sorted(
            [
                "default_transaction_read_only=on",
                f"statement_timeout={statement_timeout}",
                "temp_file_limit=256MB",
            ]
        )
        assert row[1] == conn_limit


class TestAdminRole:
    def test_admin_owns_database_and_can_create_databases(
        self, admin: psycopg.Connection, settings: WarehouseSettings
    ) -> None:
        query = (
            "SELECT pg_get_userbyid(datdba), (SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user)"
            " FROM pg_database WHERE datname = current_database()"
        )

        row = admin.execute(query).fetchone()

        assert row == (settings.admin_user, True)
