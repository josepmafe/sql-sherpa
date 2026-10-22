from __future__ import annotations

from enum import Enum
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
            key_columns = tuple(batch[0].key)
            statement = sql.SQL("UPDATE {} SET {} WHERE {}").format(
                self._table(record_type.TABLE),
                sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in value_columns),
                sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in key_columns),
            )
            params = [
                [self._db_value(u.values[c]) for c in value_columns] + [u.key[c] for c in key_columns] for u in batch
            ]
            with self._conn.cursor() as cur:
                cur.executemany(statement, params)
                matched = cur.rowcount  # psycopg sums rowcount over executemany; keys are unique

            # A lost update would silently corrupt the history, so it fails the day instead.
            if matched != len(batch):
                msg = f"{len(batch) - matched} of {len(batch)} updates of {record_type.TABLE} matched no row"
                raise LookupError(msg)

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

    @staticmethod
    def _db_value(value: object) -> object:
        match value:
            case BaseModel():
                return Jsonb(value.model_dump(mode="json", exclude_none=True))
            case Enum():
                return value.value
            case _:
                return value
