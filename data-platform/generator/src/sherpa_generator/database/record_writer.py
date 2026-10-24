from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from itertools import groupby
from typing import TYPE_CHECKING

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel

if TYPE_CHECKING:
    import psycopg

    from sherpa_generator.simulation.day_changes import DayChanges, RecordUpdate

PENDING_EVENTS_TABLE = "raw_meta.pending_events"


class RecordWriter:
    """Applies one day's changes on the caller's connection. Never commits: the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def write(self, changes: DayChanges) -> dict[str, int]:
        """Inserts (COPY), then updates, then pending deletions. Returns rows copied per table."""
        counts = {record_type.TABLE: len(records) for record_type, records in changes.inserts.items()}
        for records in changes.inserts.values():
            self._copy(records[0].TABLE, records[0].columns(), [self._row(r.copy_values()) for r in records])

        self._apply_updates(changes.updates)
        self._delete_pending(changes.pending_removals)
        return counts

    def _copy(self, table: str, columns: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
        statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            self._table(table), sql.SQL(", ").join(sql.Identifier(c) for c in columns)
        )
        with self._conn.cursor() as cur, cur.copy(statement) as copy:
            for row in rows:
                copy.write_row(row)

    def _apply_updates(self, updates: list[RecordUpdate]) -> None:
        # Consecutive updates of the same shape are batched; order across shapes is preserved.
        for (record_type, value_columns), group in groupby(updates, key=lambda u: (u.record_type, tuple(u.values))):
            batch = list(group)
            matched = sum(self._update(record_type.TABLE, value_columns, run) for run in self._distinct_key_runs(batch))

            # A lost update would silently corrupt the history, so it fails the day instead.
            if matched != len(batch):
                msg = f"{len(batch) - matched} of {len(batch)} updates of {record_type.TABLE} matched no row"
                raise LookupError(msg)

    def _update(self, table: str, value_columns: tuple[str, ...], updates: list[RecordUpdate]) -> int:
        """One set-based UPDATE (a pipelined executemany stalls ~40 ms per call). The table's own row type casts the
        JSON values, so every column keeps its type; the keys must be distinct. Returns the rows matched."""
        key_columns = tuple(updates[0].key)
        statement = sql.SQL(
            "UPDATE {table} AS t SET {values} FROM jsonb_populate_recordset(NULL::{table}, %s) AS v WHERE {keys}"
        ).format(
            table=self._table(table),
            values=sql.SQL(", ").join(sql.SQL("{0} = v.{0}").format(sql.Identifier(c)) for c in value_columns),
            keys=sql.SQL(" AND ").join(sql.SQL("t.{0} = v.{0}").format(sql.Identifier(c)) for c in key_columns),
        )
        rows = [{**u.key, **{c: u.values[c] for c in value_columns}} for u in updates]
        with self._conn.cursor() as cur:
            cur.execute(statement, [Jsonb(rows, dumps=_dumps)])
            return cur.rowcount

    @staticmethod
    def _distinct_key_runs(updates: list[RecordUpdate]) -> list[list[RecordUpdate]]:
        """Splits where a key repeats: one UPDATE ... FROM applies only one of two rows for the same key."""
        runs: list[list[RecordUpdate]] = [[]]
        seen: set[tuple[object, ...]] = set()
        for update in updates:
            key = tuple(update.key.values())
            if key in seen:
                runs.append([])
                seen.clear()

            runs[-1].append(update)
            seen.add(key)
        return runs

    def _delete_pending(self, event_ids: list[str]) -> None:
        if event_ids:
            self._conn.execute(
                sql.SQL("DELETE FROM {} WHERE event_id = ANY(%s)").format(self._table(PENDING_EVENTS_TABLE)),
                [event_ids],
            )

    @staticmethod
    def _table(qualified: str) -> sql.Identifier:
        schema, table = qualified.split(".")
        return sql.Identifier(schema, table)

    @staticmethod
    def _row(values: tuple[object, ...]) -> tuple[object, ...]:
        return tuple(Jsonb(v) if isinstance(v, dict) else v for v in values)


def _dumps(obj: object) -> str:
    return json.dumps(obj, default=_json_value)


def _json_value(value: object) -> object:
    """Update values as JSON; jsonb_populate_recordset parses the strings back with each column's type."""
    match value:
        case BaseModel():
            return value.model_dump(mode="json", exclude_none=True)
        case datetime() | date():
            return value.isoformat()
        case Decimal():
            return str(value)
        case _:
            msg = f"cannot write {type(value).__name__} in an update"
            raise TypeError(msg)
