"""Read-only access to the warehouse as the `sherpa_agent` role (agent spec §4.2 items 2 to 5).

Every call is one read-only transaction with its own `statement_timeout`, a named server-side cursor and a row cap.
Queries always go over Postgres's extended protocol, which accepts a single statement per message, so a string with
several statements is refused by Postgres itself even if `sql_guard` and Postgres ever split a query differently.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg
from psycopg import errors, sql
from psycopg_pool import ConnectionPool, PoolTimeout

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_mcp.settings import McpSettings

STATEMENT_TIMEOUT = "5s"
# Postgres times each FETCH on its own, so fetching the rows has its own 5 s budget as well.
FETCH_DEADLINE_SECONDS = 5.0
# Every message for the model starts with a stable code: [timeout], [sql] (Postgres refused the query) or
# [unreachable] (no connection to the warehouse).
TIMEOUT_MESSAGE = "[timeout] the query took longer than 5 s; aggregate or filter more narrowly"
UNREACHABLE_MESSAGE = "[unreachable] the warehouse is unreachable; is `make up` running?"
CONNECTION_EXCEPTION_CLASS = "08"  # SQLSTATE class: connection exception
# Data exceptions (SQLSTATE class 22) quote the offending value (`invalid input syntax for type integer: "a@b.com"`),
# which can be PII, so the model (and the UI, through `data_error_message`) gets the exception's name and a remedy
# instead of Postgres's message.
DATA_ERROR_HINTS = {
    "22012": "wrap the denominator in nullif(x, 0)",  # division_by_zero
    "22P02": "check the literal format",  # invalid_text_representation
    "22007": "check the literal format",  # invalid_datetime_format
    "22008": "check the literal format",  # datetime_field_overflow
}
DATA_ERROR_HINT = "check the column types and use explicit, safe casts"
POOL_TIMEOUT_SECONDS = 10.0  # how long a call waits for a free connection
FETCH_BATCH_ROWS = 20  # rows per FETCH: one round trip per row was ~11x slower for 100 rows
# After a row this long (before cutting), the rest is fetched one row at a time, so at most one such row is in memory.
LARGE_ROW_CHARS = 1_000_000
MAX_CELL_CHARS = 10_000  # one text, bytes (hex), array or JSON cell; keeps a result small enough for the model
MAX_RESULT_CHARS = 50_000  # all cells of one model-facing result together; rows past it are dropped as truncated
CUT_MARKER = f" …[cut at {MAX_CELL_CHARS} chars]"
# Model-written queries fetched at the same time; each can hold a few large rows, so the rest wait for a slot.
MAX_HEAVY_FETCHES = 2
HEAVY_FETCH_WAIT_SECONDS = 10.0
BUSY_MESSAGE = "[timeout] the server is busy with other queries; retry in a few seconds"
NESTED_MESSAGE = "[sql] a value is nested too deeply to return; select scalar columns"
SIMILARITY_THRESHOLD = "0.2"  # pg_trgm `%`: similar to the whole value
WORD_SIMILARITY_THRESHOLD = "0.6"  # pg_trgm `<%`: close to a word of the value (Postgres's default, set explicitly)
POOL_MIN_SIZE = 1
DEFAULT_MAX_ROWS = 100
EXPLAIN_PREFIX = "explain (analyze false) "

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
    column_types: tuple[str, ...] = ()


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


def cap_cell(value: object) -> object:
    """Cut a converted cell longer than `MAX_CELL_CHARS`; an array or document is measured and cut as JSON text."""
    match value:
        case str() if len(value) > MAX_CELL_CHARS:
            return value[:MAX_CELL_CHARS] + CUT_MARKER
        case list() | dict():
            text = json.dumps(value, ensure_ascii=False)
            return text[:MAX_CELL_CHARS] + CUT_MARKER if len(text) > MAX_CELL_CHARS else value
        case _:
            return value


def cell_chars(value: object) -> int:
    """How many characters a converted cell takes in the result the model reads."""
    match value:
        case str():
            return len(value)
        case list() | dict():
            return len(json.dumps(value, ensure_ascii=False))
        case _:
            return len(str(value))


def to_tool_error(error: psycopg.Error, prefix_chars: int = 0) -> WarehouseToolError:
    """The model's view of a database error. `prefix_chars`: how much text the server put before the model's query
    (the cursor's DECLARE, EXPLAIN), so the error position points into the query the model wrote."""
    sqlstate = error.sqlstate or ""
    if isinstance(error, errors.QueryCanceled):
        return WarehouseToolError(TIMEOUT_MESSAGE)
    if isinstance(error, PoolTimeout) or sqlstate.startswith(CONNECTION_EXCEPTION_CLASS):
        return WarehouseToolError(UNREACHABLE_MESSAGE)
    if isinstance(error, psycopg.OperationalError) and not sqlstate:  # the connection failed or broke: no SQLSTATE
        return WarehouseToolError(UNREACHABLE_MESSAGE)
    if isinstance(error, errors.DataError):
        return WarehouseToolError(f"[sql] {data_error_message(error)}")

    message = f"[sql] {error.diag.message_primary or str(error)}"
    position = int(error.diag.statement_position or 0) - prefix_chars
    if position > 0:
        message += f" (at character {position})"
    if error.diag.message_hint:
        message += f"; {error.diag.message_hint}"
    return WarehouseToolError(message)


def data_error_message(error: errors.DataError) -> str:
    """A data exception without Postgres's text, which can quote the offending value: its name and a remedy."""
    hint = DATA_ERROR_HINTS.get(error.sqlstate or "", DATA_ERROR_HINT)
    return f"a value could not be converted or computed ({type(error).__name__}); {hint}"


class AgentDatabase:
    def __init__(self, settings: McpSettings) -> None:
        self._pool = ConnectionPool(
            settings.agent_conninfo(),
            min_size=POOL_MIN_SIZE,
            max_size=settings.mcp_pool_size,
            open=True,
            timeout=POOL_TIMEOUT_SECONDS,
            # A connection Postgres dropped (restart, idle timeout) is replaced before a call gets it.
            check=ConnectionPool.check_connection,
        )
        self._heavy_fetches = threading.BoundedSemaphore(MAX_HEAVY_FETCHES)

    def wait_until_connected(self, timeout: float) -> None:
        """Block until the pool holds a connection; `[unreachable]` if none opens in `timeout` seconds."""
        try:
            self._pool.wait(timeout=timeout)
        except PoolTimeout as error:
            raise WarehouseToolError(UNREACHABLE_MESSAGE) from error

    def fetch(
        self,
        query: Query,
        params: Sequence[object] = (),
        max_rows: int = DEFAULT_MAX_ROWS,
        *,
        set_similarity_threshold: bool = False,
    ) -> QueryResult:
        """The server's own queries, whose size it controls: values come back as psycopg loads them."""
        return self._fetch(query, params, max_rows, set_similarity_threshold=set_similarity_threshold, for_model=False)

    def fetch_for_model(self, query: Query, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
        """A query whose rows go to the model: JSON-safe values, each cell cut to `MAX_CELL_CHARS`, and rows past
        `MAX_RESULT_CHARS` dropped (`truncated`). At most `MAX_HEAVY_FETCHES` run at once."""
        if not self._heavy_fetches.acquire(timeout=HEAVY_FETCH_WAIT_SECONDS):
            raise WarehouseToolError(BUSY_MESSAGE)
        try:
            return self._fetch(query, (), max_rows, set_similarity_threshold=False, for_model=True)
        finally:
            self._heavy_fetches.release()

    def explain(self, sql_text: str) -> None:
        """Plan `sql_text` without running it; raises `WarehouseToolError` with Postgres's message if it cannot."""
        try:
            with self._pool.connection() as conn, conn.transaction():
                self._begin(conn)
                # prepare=True forces the extended protocol, as for `fetch`. The ignore: psycopg types sql.SQL as
                # taking a LiteralString, and the model's query is a runtime string (already checked by the guard).
                conn.execute(sql.SQL(EXPLAIN_PREFIX + "{}").format(sql.SQL(sql_text)), prepare=True)  # type: ignore[arg-type]
        except psycopg.Error as error:
            raise to_tool_error(error, prefix_chars=len(EXPLAIN_PREFIX)) from error

    def scalar(self, query: Query, params: Sequence[object] = ()) -> object:
        result = self.fetch(query, params, max_rows=1)
        if not result.rows:
            raise WarehouseToolError("the query returned no rows")
        return result.rows[0][0]

    def close(self) -> None:
        self._pool.close()

    def _fetch(
        self,
        query: Query,
        params: Sequence[object],
        max_rows: int,
        *,
        set_similarity_threshold: bool,
        for_model: bool,
    ) -> QueryResult:
        started = time.monotonic()
        rows: list[tuple[object, ...]] = []
        truncated = False
        result_chars = 0
        batch_rows = FETCH_BATCH_ROWS
        cursor_name = f"sherpa_{uuid.uuid4().hex}"
        try:
            with self._pool.connection() as conn, conn.transaction():
                self._begin(conn, set_similarity_threshold=set_similarity_threshold)
                deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
                # A named cursor declares the query with the extended protocol (`force_extended`), even without params.
                with conn.cursor(name=cursor_name) as cursor:
                    cursor.execute(query, params or None)
                    columns = tuple(column.name for column in cursor.description or ())
                    column_types = tuple(column.type_display for column in cursor.description or ())
                    while not truncated:
                        if time.monotonic() > deadline:
                            raise WarehouseToolError(TIMEOUT_MESSAGE)

                        # Never more than one row past the cap: that row only tells whether there were more.
                        batch = cursor.fetchmany(min(batch_rows, max_rows + 1 - len(rows)))
                        if not batch:
                            break

                        for raw in batch:
                            if len(rows) == max_rows:
                                truncated = True
                                break
                            if not for_model:
                                rows.append(tuple(raw))
                                continue

                            row, row_chars, full_chars = _model_row(raw)
                            if full_chars > LARGE_ROW_CHARS:
                                batch_rows = 1
                            if result_chars + row_chars > MAX_RESULT_CHARS:
                                truncated = True
                                break

                            result_chars += row_chars
                            rows.append(row)
        except psycopg.Error as error:
            # psycopg sends the query as `DECLARE "<name>" CURSOR FOR <query>`.
            raise to_tool_error(error, prefix_chars=len(f'DECLARE "{cursor_name}" CURSOR FOR ')) from error
        except RecursionError as error:  # a deeply nested array or jsonb value, in psycopg's loader or in ours
            raise WarehouseToolError(NESTED_MESSAGE) from error
        return QueryResult(
            columns=columns,
            rows=tuple(rows),
            returned_rows=len(rows),
            truncated=truncated,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            column_types=column_types,
        )

    @staticmethod
    def _begin(conn: psycopg.Connection, *, set_similarity_threshold: bool = False) -> None:
        # Per transaction, so a session default switched off by a query (`set_config`) cannot make a later one writable.
        conn.execute(sql.SQL("set transaction read only"))
        conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT)))
        if set_similarity_threshold:
            conn.execute(
                sql.SQL("set local pg_trgm.similarity_threshold = {}").format(sql.Literal(SIMILARITY_THRESHOLD))
            )
            conn.execute(
                sql.SQL("set local pg_trgm.word_similarity_threshold = {}").format(
                    sql.Literal(WORD_SIMILARITY_THRESHOLD)
                )
            )


def _model_row(raw: Sequence[object]) -> tuple[tuple[object, ...], int, int]:
    """The row as the model gets it, its length in characters, and its length before the cells were cut."""
    converted = [json_safe(value) for value in raw]
    capped = tuple(cap_cell(value) for value in converted)
    return capped, sum(cell_chars(value) for value in capped), sum(cell_chars(value) for value in converted)
