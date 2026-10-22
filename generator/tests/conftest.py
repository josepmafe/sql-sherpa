from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql
from sherpa_generator.database.connection import Database
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.settings import GeneratorSettings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def temp_database() -> Iterator[Database]:
    """A throwaway database with the raw schema applied, dropped afterwards."""
    settings = GeneratorSettings()
    name = f"sherpa_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

    try:
        db = Database(settings, dbname=name)
        RawSchema(db).create()
        yield db
    finally:
        with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
