"""What the agent and UI roles can and cannot read or create in the built warehouses.

The main database tests need `make build-prod` (and `make build` for the dev_marts check); the eval database
tests need `make eval-db`. Every denied read is preceded by an admin-side check that the relation exists, so a
missing table cannot pass for a missing privilege.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import errors, sql

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Database, Role, WarehouseSettings

MANIFEST = Path(__file__).parents[2] / "dbt" / "target-prod" / "manifest.json"
LARGE_OBJECT_FUNCTIONS = (
    "lo_create(oid)",
    "lo_creat(integer)",
    "lo_from_bytea(oid, bytea)",
    "lo_open(oid, integer)",
    "lowrite(integer, bytea)",
    "lo_put(oid, bigint, bytea)",
)
# Spec §6.5: the marts built so far. Hard-coded on purpose: the MCP server and UI suites import it, and a list read
# from the same manifest as their catalogs would pass when a mart disappears.
MARTS = (
    "dim_customers",
    "dim_date",
    "dim_products",
    "dim_promotions",
    "fct_order_discounts",
    "fct_order_items",
    "fct_orders",
    "fct_refunds",
    "fct_returns",
    "fct_sessions",
)
NOT_EXPOSED = (
    ("staging", "stg_shop__orders"),
    ("intermediate", "int_order_items__discount_allocated"),
    ("seeds", "countries"),
    ("raw_shop", "orders"),
    ("snapshots", "snap_product_variants"),
    ("test_failures", "assert_buyer_revenue_pareto"),
)
TRIGRAM_INDEXES = [
    "dim_products_brand_trgm",
    "dim_products_category_l1_trgm",
    "dim_products_category_l2_trgm",
    "dim_products_category_l3_trgm",
    "dim_products_product_name_trgm",
]

pytestmark = pytest.mark.warehouse


@pytest.fixture(params=["main", "eval"])
def database(request: pytest.FixtureRequest) -> Database:
    return request.param


def _login(settings: WarehouseSettings, database: Database, role: Role) -> Iterator[psycopg.Connection]:
    # autocommit: a denied query must not abort the session for the next statement.
    with psycopg.connect(settings.conninfo(database, role), autocommit=True) as conn:
        yield conn


@pytest.fixture(params=["agent", "ui"])
def reader(
    request: pytest.FixtureRequest, settings: WarehouseSettings, database: Database
) -> Iterator[psycopg.Connection]:
    yield from _login(settings, database, request.param)


@pytest.fixture
def ui(settings: WarehouseSettings, database: Database) -> Iterator[psycopg.Connection]:
    yield from _login(settings, database, "ui")


@pytest.fixture
def agent(settings: WarehouseSettings, database: Database) -> Iterator[psycopg.Connection]:
    yield from _login(settings, database, "agent")


def test_manifest_marts_are_the_expected_marts() -> None:
    assert MANIFEST.exists(), f"no manifest at {MANIFEST}; run make build-prod"
    nodes = json.loads(MANIFEST.read_text(encoding="utf-8"))["nodes"].values()

    built = sorted(node["name"] for node in nodes if node["resource_type"] == "model" and node["schema"] == "marts")

    assert built == sorted(MARTS)


class TestReaderAccess:
    @pytest.mark.parametrize("table", MARTS)
    def test_reads_every_mart(self, reader: psycopg.Connection, table: str) -> None:
        query = sql.SQL("select count(*) from marts.{}").format(sql.Identifier(table))

        row = reader.execute(query).fetchone()

        assert row is not None
        assert row[0] > 0

    @pytest.mark.parametrize(("schema", "table"), NOT_EXPOSED)
    def test_cannot_read_non_mart_schemas(
        self, admin: psycopg.Connection, reader: psycopg.Connection, schema: str, table: str
    ) -> None:
        assert _exists(admin, schema, table)
        query = sql.SQL("select count(*) from {}").format(sql.Identifier(schema, table))

        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute(query)

    @pytest.mark.parametrize("database", ["main"], indirect=True)
    def test_cannot_read_dev_marts(self, admin: psycopg.Connection, reader: psycopg.Connection) -> None:
        # dbt's grants config gives the reader roles SELECT on dev_marts tables too; only the missing USAGE blocks it.
        assert _exists(admin, "dev_marts", "fct_orders")
        privileges = admin.execute(
            "select has_table_privilege(%(role)s, 'dev_marts.fct_orders', 'SELECT'),"
            " has_schema_privilege(%(role)s, 'dev_marts', 'USAGE')",
            {"role": _current_user(reader)},
        ).fetchone()
        assert privileges == (True, False)
        query = "select count(*) from dev_marts.fct_orders"

        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute(query)

    def test_cannot_write_to_any_mart_or_ops_relation_or_create_in_the_exposed_schemas(
        self, admin: psycopg.Connection, reader: psycopg.Connection
    ) -> None:
        # One query per reader: INSERT/UPDATE/DELETE/TRUNCATE on every relation of marts and ops, and CREATE on the
        # schemas the reader can see or whose objects (extensions) it uses. The read-only options are defence in depth.
        query = """
            SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('marts', 'ops') AND c.relkind IN ('r', 'v', 'm', 'p', 'f')),
                   (SELECT coalesce(array_agg(n.nspname || '.' || c.relname ORDER BY c.relname), '{}')
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('marts', 'ops') AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
                      AND has_table_privilege(%(role)s, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')),
                   (SELECT coalesce(array_agg(nspname ORDER BY nspname), '{}') FROM pg_namespace
                    WHERE nspname IN ('marts', 'extensions', 'ops') AND has_schema_privilege(%(role)s, oid, 'CREATE'))
        """

        row = admin.execute(query, {"role": _current_user(reader)}).fetchone()  # type: ignore[call-overload]

        assert row is not None
        inspected, writable, creatable = row
        assert inspected >= len(MARTS)
        assert (writable, creatable) == ([], [])

    def test_has_no_usage_on_public(self, reader: psycopg.Connection) -> None:
        query = "select has_schema_privilege('public', 'USAGE')"

        row = reader.execute(query).fetchone()

        assert row == (False,)

    def test_cannot_create_temporary_tables(self, admin: psycopg.Connection, reader: psycopg.Connection) -> None:
        query = "select has_database_privilege(%s, current_database(), 'TEMPORARY')"

        row = admin.execute(query, (_current_user(reader),)).fetchone()

        assert row == (False,)

    @pytest.mark.parametrize("function", LARGE_OBJECT_FUNCTIONS)
    def test_cannot_execute_large_object_functions(
        self, admin: psycopg.Connection, reader: psycopg.Connection, function: str
    ) -> None:
        # The eval database inherits the revoke from template1; the main database got it from the init script.
        query = "select has_function_privilege(%s, %s, 'EXECUTE')"

        row = admin.execute(query, (_current_user(reader), function)).fetchone()

        assert row == (False,)

    def test_can_use_trigram_similarity_unqualified(self, reader: psycopg.Connection) -> None:
        query = "select similarity('running', 'runing') > 0.3"

        row = reader.execute(query).fetchone()

        assert row == (True,)

    def test_reads_persisted_comments(self, reader: psycopg.Connection) -> None:
        query = "select obj_description('marts.fct_orders'::regclass)"

        row = reader.execute(query).fetchone()

        assert row is not None
        assert row[0] is not None


class TestRunResultsAccess:
    # ops is built by the prod target only, so the main database is enough.
    @pytest.mark.parametrize("database", ["main"], indirect=True)
    def test_ui_reads_run_results_and_agent_does_not(
        self, admin: psycopg.Connection, ui: psycopg.Connection, agent: psycopg.Connection
    ) -> None:
        assert _exists(admin, "ops", "dbt_run_results")
        query = "select count(*) from ops.dbt_run_results"

        row = ui.execute(query).fetchone()

        assert row is not None
        assert row[0] > 0
        with pytest.raises(errors.InsufficientPrivilege):
            agent.execute(query)


class TestTrigramIndexes:
    # The planner may still seq-scan a table of ~1,500 rows, so the check is that the indexes exist, not the plan.
    def test_dim_products_has_every_trigram_index(self, admin: psycopg.Connection) -> None:
        query = (
            "select indexname from pg_indexes"
            " where schemaname = 'marts' and tablename = 'dim_products' and indexname like '%\\_trgm'"
            " order by indexname"
        )

        rows = admin.execute(query).fetchall()

        assert [row[0] for row in rows] == TRIGRAM_INDEXES


def _current_user(conn: psycopg.Connection) -> str:
    row = conn.execute("select current_user").fetchone()
    assert row is not None
    return row[0]


def _exists(admin: psycopg.Connection, schema: str, table: str) -> bool:
    name = sql.Identifier(schema, table).as_string(admin)
    row = admin.execute("select to_regclass(%s) is not null", (name,)).fetchone()
    return row == (True,)
