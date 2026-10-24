"""Read-only access to the warehouse as the `sherpa_agent` role (agent spec §4.2 items 2 to 5).

Every call is one read-only transaction with its own `statement_timeout`, a named server-side cursor and a row cap.
Queries always go over Postgres's extended protocol, which accepts a single statement per message, so a string with
several statements is refused by Postgres itself even if `sql_guard` and Postgres ever split a query differently.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg
from psycopg import errors, sql
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_mcp.settings import McpSettings

STATEMENT_TIMEOUT = "5s"
TIMEOUT_MESSAGE = "the query took longer than 5 s; aggregate or filter more narrowly"
SIMILARITY_THRESHOLD = "0.2"
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4
DEFAULT_MAX_ROWS = 100

Query = str | sql.SQL | sql.Composed  # the forms psycopg accepts for a statement


class WarehouseToolError(Exception):
    """A failure whose message is meant for the model to read and act on."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    returned_rows: int
    truncated: bool
    elapsed_ms: int


def json_safe(value: object) -> object:
    """Convert what Postgres returns into values `json.dumps` accepts, recursing into arrays and jsonb."""
    match value:
        case None | bool() | int() | str():
            return value
        case float():
            return value if math.isfinite(value) else str(value)
        case Decimal() | uuid.UUID() | dt.timedelta() | ipaddress.IPv4Address() | ipaddress.IPv6Address():
            return str(value)
        case ipaddress.IPv4Interface() | ipaddress.IPv6Interface() | ipaddress.IPv4Network() | ipaddress.IPv6Network():
            return str(value)
        case dt.date() | dt.time():  # datetime is a date
            return value.isoformat()
        case bytes() | bytearray() | memoryview():
            return bytes(value).hex()
        case list() | tuple():
            return [json_safe(item) for item in value]
        case dict():
            return {str(key): json_safe(item) for key, item in value.items()}
        case _:
            return str(value)


def to_tool_error(error: psycopg.Error) -> WarehouseToolError:
    if isinstance(error, errors.QueryCanceled):
        return WarehouseToolError(TIMEOUT_MESSAGE)
    if isinstance(error, errors.DataError):
        # Data exceptions quote the offending value (`invalid input syntax for type integer: "a@b.com"`): PII.
        return WarehouseToolError(
            f"a value could not be converted or computed ({type(error).__name__}); "
            "check the column types and use explicit, safe casts"
        )
    return WarehouseToolError(error.diag.message_primary or str(error))


class AgentDatabase:
    def __init__(self, settings: McpSettings) -> None:
        self._pool = ConnectionPool(
            settings.agent_conninfo(), min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, open=True, timeout=10
        )

    def fetch(
        self,
        query: Query,
        params: Sequence[object] = (),
        max_rows: int = DEFAULT_MAX_ROWS,
        *,
        set_similarity_threshold: bool = False,
    ) -> QueryResult:
        started = time.monotonic()
        try:
            with self._pool.connection() as conn, conn.transaction():
                self._begin(conn, set_similarity_threshold=set_similarity_threshold)
                # A named cursor declares the query with the extended protocol (`force_extended`), even without params.
                with conn.cursor(name=f"sherpa_{uuid.uuid4().hex}") as cursor:
                    cursor.execute(query, params or None)
                    fetched = cursor.fetchmany(max_rows + 1)
                    columns = tuple(column.name for column in cursor.description or ())
        except psycopg.Error as error:
            raise to_tool_error(error) from error
        rows = tuple(tuple(json_safe(value) for value in row) for row in fetched[:max_rows])
        return QueryResult(
            columns=columns,
            rows=rows,
            returned_rows=len(rows),
            truncated=len(fetched) > max_rows,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    def explain(self, sql_text: str) -> None:
        """Plan `sql_text` without running it; raises `WarehouseToolError` with Postgres's message if it cannot."""
        try:
            with self._pool.connection() as conn, conn.transaction():
                self._begin(conn)
                # prepare=True forces the extended protocol, as for `fetch`.
                conn.execute(sql.SQL("explain (analyze false) {}").format(sql.SQL(sql_text)), prepare=True)  # type: ignore[arg-type]
        except psycopg.Error as error:
            raise to_tool_error(error) from error

    def scalar(self, query: Query, params: Sequence[object] = ()) -> object:
        result = self.fetch(query, params, max_rows=1)
        if not result.rows:
            raise WarehouseToolError("the query returned no rows")
        return result.rows[0][0]

    def close(self) -> None:
        self._pool.close()

    @staticmethod
    def _begin(conn: psycopg.Connection, *, set_similarity_threshold: bool = False) -> None:
        conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT)))
        if set_similarity_threshold:
            conn.execute(
                sql.SQL("set local pg_trgm.similarity_threshold = {}").format(sql.Literal(SIMILARITY_THRESHOLD))
            )
