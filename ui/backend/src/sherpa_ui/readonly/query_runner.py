"""The read-only path for the SQL people write in the UI (UI spec §5): guard, `sherpa_ui` role, row cap and result
size, masking, server-side sort and filter, cancel, and error positions in the SQL as written."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import psycopg
import sqlglot
from psycopg import errors, sql
from psycopg.postgres import types as postgres_types
from pydantic import BaseModel, ConfigDict, JsonValue
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_mcp.database import data_error_message
from sherpa_mcp.masking import PiiMasker
from sherpa_mcp.sql_guard import DIALECT, SqlGuard
from sqlglot.errors import SqlglotError

from sherpa_ui.readonly.bounded_result import BoundedResult
from sherpa_ui.readonly.sort_filter import (
    DuplicateColumnsError,
    PiiColumnError,
    UnknownColumnError,
    build_wrapped_query,
    strip_terminators,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sherpa_mcp.catalog import MartCatalog, MartCatalogLoader

    from sherpa_ui.readonly.sort_filter import FilterSpec, SortSpec
    from sherpa_ui.settings import UiSettings

logger = logging.getLogger(__name__)

ColumnType = Literal["number", "text", "date", "timestamp", "boolean", "other"]
QueryMode = Literal["client", "server"]
QueryErrorCode = Literal[
    "guard_rejected",
    "sql_error",
    "timeout",
    "cancelled",
    "duplicate_query_id",
    "busy",
    "catalog_unavailable",
    "database_unavailable",
    "too_many_connections",
]

# What the grid sorts and filters a column as (a Decimal arrives as a string); any other type is "other".
COLUMN_TYPES: Final[dict[str, ColumnType]] = {
    "int2": "number",
    "int4": "number",
    "int8": "number",
    "numeric": "number",
    "float4": "number",
    "float8": "number",
    "text": "text",
    "varchar": "text",
    "bpchar": "text",
    "name": "text",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamp",
    "bool": "boolean",
}
# One connection serves one run, so one cursor name is enough. psycopg declares it as `DECLARE "<name>" CURSOR FOR
# <query>`, and Postgres counts error positions from the start of that text.
CURSOR_NAME: Final = "sherpa_ui"
DECLARE_PREFIX: Final = f'DECLARE "{CURSOR_NAME}" CURSOR FOR '
PROBE_SUFFIX: Final = " limit 0"  # server mode learns the result's column names without running the query
# Rows a FETCH asks for: 1,000 fct_orders rows take ~20 ms in batches of 100, against ~10 ms in a single FETCH.
FETCH_BATCH_ROWS: Final = 100
# Cells a FETCH asks for, so a wide result comes in fewer rows at a time: a batch of 10,000-character cells (lpad's
# longest) stays near 100 MB however many columns the query has.
FETCH_BATCH_CELLS: Final = 10_000
CANCEL_TIMEOUT_SECONDS: Final = 5.0
# UI spec §5. Runs holding a connection at once, below the sherpa_ui role's CONNECTION LIMIT 10
# (data-platform/postgres/roles.sql), so the health check still gets one while the runner is full.
MAX_CONCURRENT_QUERIES: Final = 8
# SQLSTATE 53300 (too_many_connections). libpq gives no SQLSTATE for a refused connection, so it is recognised by
# Postgres's messages for it: a role's or database's CONNECTION LIMIT, max_connections, the reserved slots.
TOO_MANY_CONNECTIONS_MARKERS: Final = (
    "too many connections for",
    "too many clients already",
    "remaining connection slots are reserved",
)
CONNECTION_EXCEPTION_CLASS: Final = "08"  # SQLSTATE class: connection exception
# In that class, but Postgres answers it for a statement it cannot bind (`$1` without a value) on a working connection.
PROTOCOL_VIOLATION: Final = "08P01"
CANCELLED_MESSAGE: Final = "the query was cancelled"
TIMEOUT_MESSAGE: Final = "the query ran longer than {seconds} s and was stopped; filter or aggregate more narrowly"
DUPLICATE_MESSAGE: Final = "a query with id {query_id} is already running"
BUSY_MESSAGE: Final = (
    f"too many queries are running (at most {MAX_CONCURRENT_QUERIES} at once); wait for one to finish or cancel one"
)
UNREACHABLE_MESSAGE: Final = "the warehouse is unreachable ({reason}); is `make up` running?"
TOO_MANY_CONNECTIONS_MESSAGE: Final = (
    "the warehouse refused a connection: too many are open for the UI's login ({reason}); another client of that "
    "login (a second UI, a test run) holds some, so wait for its queries to finish and run again"
)
FORMAT_ERROR_MESSAGE: Final = "could not format the query: {reason}"
FORMAT_SEPARATOR: Final = ";\n\n"
_TYPE_BY_OID: Final[dict[int, ColumnType]] = {postgres_types[name].oid: kind for name, kind in COLUMN_TYPES.items()}


class UiQueryError(Exception):
    """A failure the UI shows as it is: a stable `code`, a message, and, for Postgres errors that have one, the
    0-based character offset in the SQL as the user wrote it."""

    def __init__(self, code: QueryErrorCode, message: str, position: int | None = None) -> None:
        super().__init__(message)
        self.code: QueryErrorCode = code
        self.message = message
        self.position = position


class QueryOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_id: str
    columns: tuple[str, ...]
    column_types: tuple[ColumnType, ...]
    rows: tuple[tuple[JsonValue, ...], ...]  # JSON-safe, PII-masked, and each cell cut to 10,000 characters
    returned_rows: int
    truncated: bool
    elapsed_ms: int
    mode: QueryMode


class QueryRunner:
    """Runs each query on a connection of its own as the read-only `sherpa_ui` role, and closes it afterwards; safe to
    call from several threads at once, of which at most `MAX_CONCURRENT_QUERIES` hold a connection.

    A connection never outlives its run, so a late `cancel` cannot reach another query. A run that `cancel` reached
    while it was registered always ends `cancelled`, even when the cancel request found Postgres between two
    statements with nothing to cancel.
    """

    def __init__(self, settings: UiSettings, loader: MartCatalogLoader) -> None:
        self._settings = settings
        self._loader = loader
        self._lock = threading.Lock()
        # Both guarded by _lock. A query_id maps to None while its run is still connecting.
        self._running: dict[str, psycopg.Connection | None] = {}
        self._cancelled: set[str] = set()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_QUERIES)

    def run(
        self, query_id: str, sql_text: str, sort: Sequence[SortSpec] = (), filters: Sequence[FilterSpec] = ()
    ) -> QueryOutcome:
        """Run `sql_text` as written (client mode), or wrapped to sort and filter all of its rows on the server when
        `sort` or `filters` is given (server mode).

        Raises `UiQueryError` (`busy`, without connecting, while `MAX_CONCURRENT_QUERIES` runs hold a connection); in
        server mode also `DuplicateColumnsError` (the caller falls back to client mode), `UnknownColumnError` for a
        sort or filter column that is not in the result and `PiiColumnError` for one that is masked. Whatever else went
        wrong, a run that `cancel` reached raises `UiQueryError` with code `cancelled`.
        """
        started = time.monotonic()
        mode: QueryMode = "server" if sort or filters else "client"
        self._reserve(query_id)  # first, so a cancel sent right after the request is never lost
        conn: psycopg.Connection | None = None
        holds_slot = False
        try:
            catalog = self._load_catalog()
            masker = PiiMasker(catalog.pii_columns())
            self._check_guard(catalog, sql_text)
            self._raise_if_cancelled(query_id)  # before taking a slot and a connection the run would not use
            self._take_slot()
            holds_slot = True
            conn = self._connect()
            self._attach(query_id, conn)
            fetched = self._execute(conn, query_id, strip_terminators(sql_text), sort, filters, masker)
        except (UiQueryError, DuplicateColumnsError, UnknownColumnError, PiiColumnError) as error:
            if (isinstance(error, UiQueryError) and error.code == "cancelled") or not self._is_cancelled(query_id):
                raise

            raise UiQueryError("cancelled", CANCELLED_MESSAGE) from error
        finally:
            cancelled = self._release(query_id)
            if conn is not None:
                conn.close()

            if holds_slot:
                self._slots.release()

        if cancelled:
            raise UiQueryError("cancelled", CANCELLED_MESSAGE)

        outcome = QueryOutcome(
            query_id=query_id,
            columns=fetched.columns,
            column_types=fetched.column_types,
            rows=fetched.rows,
            returned_rows=len(fetched.rows),
            truncated=fetched.truncated,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            mode=mode,
        )
        logger.info(
            "query %s returned %d rows (truncated: %s) in %d ms, %s mode",
            query_id,
            outcome.returned_rows,
            outcome.truncated,
            outcome.elapsed_ms,
            mode,
        )
        return outcome

    def cancel(self, query_id: str) -> None:
        """Cancel a running query; a no-op for an unknown or finished `query_id`."""
        with self._lock:
            if query_id not in self._running:
                return

            self._cancelled.add(query_id)
            conn = self._running[query_id]
            if conn is None:
                return  # still starting: the run stops before it executes anything

            # Under the lock, so the run cannot close the connection while the cancel request is on its way. That
            # holds the lock during network I/O, for CANCEL_TIMEOUT_SECONDS at most.
            try:
                conn.cancel_safe(timeout=CANCEL_TIMEOUT_SECONDS)
            except psycopg.OperationalError as error:
                logger.warning("could not cancel query %s: %s", query_id, error)

    def running_query_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._running)

    def format(self, sql_text: str) -> str:
        """`sql_text` pretty-printed by sqlglot (Postgres dialect), never executed. Text with no statement in it
        (empty, or only comments, which sqlglot would drop) comes back unchanged."""
        try:
            statements = sqlglot.transpile(sql_text, read=DIALECT, write=DIALECT, pretty=True)
        except (SqlglotError, RecursionError) as error:
            raise UiQueryError("sql_error", FORMAT_ERROR_MESSAGE.format(reason=_first_line(error))) from error

        return FORMAT_SEPARATOR.join(statement for statement in statements if statement) or sql_text

    def _reserve(self, query_id: str) -> None:
        with self._lock:
            if query_id in self._running:
                raise UiQueryError("duplicate_query_id", DUPLICATE_MESSAGE.format(query_id=query_id))

            self._running[query_id] = None

    def _load_catalog(self) -> MartCatalog:
        try:
            return self._loader.load()
        except CatalogUnavailable as error:
            raise UiQueryError("catalog_unavailable", str(error)) from error

    def _check_guard(self, catalog: MartCatalog, sql_text: str) -> None:
        guard = SqlGuard(catalog.mart_columns()).check(sql_text)
        if not guard.ok:
            raise UiQueryError("guard_rejected", "\n".join(guard.errors))

    def _take_slot(self) -> None:
        if not self._slots.acquire(blocking=False):
            raise UiQueryError("busy", BUSY_MESSAGE)

    def _connect(self) -> psycopg.Connection:
        try:
            return psycopg.connect(self._settings.ui_conninfo(), autocommit=True)
        except psycopg.OperationalError as error:
            reason = _first_line(error)
            if any(marker in str(error) for marker in TOO_MANY_CONNECTIONS_MARKERS):
                message = TOO_MANY_CONNECTIONS_MESSAGE.format(reason=reason)
                raise UiQueryError("too_many_connections", message) from error

            raise UiQueryError("database_unavailable", UNREACHABLE_MESSAGE.format(reason=reason)) from error

    def _attach(self, query_id: str, conn: psycopg.Connection) -> None:
        with self._lock:
            self._running[query_id] = conn

        self._raise_if_cancelled(query_id)  # a cancel sent while connecting had no connection to reach

    def _raise_if_cancelled(self, query_id: str) -> None:
        if self._is_cancelled(query_id):
            raise UiQueryError("cancelled", CANCELLED_MESSAGE)

    def _is_cancelled(self, query_id: str) -> bool:
        with self._lock:
            return query_id in self._cancelled

    def _release(self, query_id: str) -> bool:
        """Forget the run (only the run that reserved `query_id` calls this); returns whether it was cancelled."""
        with self._lock:
            del self._running[query_id]
            cancelled = query_id in self._cancelled
            self._cancelled.discard(query_id)

        return cancelled

    def _execute(
        self,
        conn: psycopg.Connection,
        query_id: str,
        statement: str,
        sort: Sequence[SortSpec],
        filters: Sequence[FilterSpec],
        masker: PiiMasker,
    ) -> _Fetched:
        try:
            with conn.transaction():
                timeout = f"{self._settings.ui_statement_timeout_s}s"
                conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(timeout)))
                if not sort and not filters:
                    # No parameters, so psycopg leaves every `%` of the user's SQL alone.
                    return self._fetch(conn, query_id, statement, None, len(DECLARE_PREFIX), len(statement), masker)

                probe = build_wrapped_query(statement, (), masker)
                with self._declared(
                    conn,
                    query_id,
                    probe.query + sql.SQL(PROBE_SUFFIX),
                    probe.params,
                    len(DECLARE_PREFIX) + probe.prefix_length,
                    len(statement),
                ) as cursor:
                    columns = [column.name for column in cursor.description or ()]

                wrapped = build_wrapped_query(statement, columns, masker, sort, filters)
                return self._fetch(
                    conn,
                    query_id,
                    wrapped.query,
                    wrapped.params,
                    len(DECLARE_PREFIX) + wrapped.prefix_length,
                    len(statement),
                    masker,
                )
        except psycopg.Error as error:  # outside a cursor (the timeout, the commit): no position in the user's SQL
            raise self._query_error(error, position=None) from error

    def _fetch(
        self,
        conn: psycopg.Connection,
        query_id: str,
        query: str | sql.Composed,
        params: Sequence[object] | None,
        offset: int,
        length: int,
        masker: PiiMasker,
    ) -> _Fetched:
        """The rows in batches, until the result is complete or `BoundedResult` says it is full. Postgres times each
        FETCH on its own, so fetching stops at the statement timeout too, checked before each FETCH, like a cancel."""
        timeout = self._settings.ui_statement_timeout_s
        with self._declared(conn, query_id, query, params, offset, length) as cursor:
            description = cursor.description or ()
            columns = tuple(column.name for column in description)
            result = BoundedResult(columns, masker, self._settings.ui_row_cap)
            batch_rows = max(1, min(FETCH_BATCH_ROWS, FETCH_BATCH_CELLS // max(len(columns), 1)))
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() > deadline:
                    raise UiQueryError("timeout", TIMEOUT_MESSAGE.format(seconds=timeout))

                self._raise_if_cancelled(query_id)
                batch = cursor.fetchmany(result.fetch_size(batch_rows))
                if not batch or not result.extend(batch):
                    break

        return _Fetched(
            columns=columns,
            column_types=tuple(_TYPE_BY_OID.get(column.type_code, "other") for column in description),
            rows=result.rows,
            truncated=result.truncated,
        )

    @contextmanager
    def _declared(
        self,
        conn: psycopg.Connection,
        query_id: str,
        query: str | sql.Composed,
        params: Sequence[object] | None,
        offset: int,
        length: int,
    ) -> Iterator[psycopg.ServerCursor[Any]]:
        """A server-side cursor for `query`, whose text holds the user's SQL (`length` characters) after `offset`
        characters of its own, counting the declaration. Errors while declaring or fetching are mapped here.

        A cancel that arrived between two statements found nothing running in Postgres, so it is checked before each.
        """
        self._raise_if_cancelled(query_id)
        try:
            with conn.cursor(name=CURSOR_NAME) as cursor:
                cursor.execute(query, params)
                yield cursor
        except psycopg.Error as error:
            position = user_position(error.diag.statement_position, offset=offset, length=length)
            raise self._query_error(error, position) from error

    def _query_error(self, error: psycopg.Error, position: int | None) -> UiQueryError:
        """The error as the UI shows it; `run` reports it as `cancelled` instead when the run was cancelled."""
        if isinstance(error, errors.QueryCanceled):  # not cancelled by us, so the statement timeout stopped it
            return UiQueryError("timeout", TIMEOUT_MESSAGE.format(seconds=self._settings.ui_statement_timeout_s))

        if _is_connection_failure(error):
            return UiQueryError("database_unavailable", UNREACHABLE_MESSAGE.format(reason=_first_line(error)))

        if isinstance(error, errors.DataError):  # Postgres's text can quote the value, a masked one too (UI spec §5)
            return UiQueryError("sql_error", data_error_message(error), position)

        message = error.diag.message_primary or str(error)
        if error.diag.message_hint:
            message = f"{message}; {error.diag.message_hint}"

        return UiQueryError("sql_error", message, position)


def user_position(statement_position: str | None, offset: int, length: int) -> int | None:
    """Postgres's 1-based `statement_position` in the text it was sent, as a 0-based offset in the user's SQL, which
    starts after `offset` characters and is `length` long; None outside it. `length` itself is allowed: Postgres puts
    "at end of input" just past the last character."""
    if not statement_position:
        return None

    index = int(statement_position) - 1 - offset
    return index if 0 <= index <= length else None


@dataclass(frozen=True)
class _Fetched:
    columns: tuple[str, ...]
    column_types: tuple[ColumnType, ...]
    rows: tuple[tuple[object, ...], ...]  # JSON-safe, PII-masked and cut
    truncated: bool


def _is_connection_failure(error: psycopg.Error) -> bool:
    """The connection broke: SQLSTATE class 08 but a protocol violation, or an OperationalError without any SQLSTATE
    (psycopg's own)."""
    sqlstate = error.sqlstate or ""
    return (sqlstate.startswith(CONNECTION_EXCEPTION_CLASS) and sqlstate != PROTOCOL_VIOLATION) or (
        isinstance(error, psycopg.OperationalError) and not sqlstate
    )


def _first_line(error: Exception) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__
