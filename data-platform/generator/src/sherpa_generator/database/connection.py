from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import psycopg

if TYPE_CHECKING:
    from collections.abc import Generator

    from sherpa_generator.settings import GeneratorSettings


class Database:
    def __init__(self, settings: GeneratorSettings, dbname: str | None = None) -> None:
        self._conninfo = settings.conninfo(dbname)

    def connect(self) -> psycopg.Connection:
        """A new connection with autocommit off. As a context manager it commits on success and closes."""
        return psycopg.connect(self._conninfo)

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection]:
        """One connection, one transaction: commit on success, roll back on any exception."""
        with self.connect() as conn:
            yield conn
