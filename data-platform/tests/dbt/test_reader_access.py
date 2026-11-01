"""What the agent and UI roles can and cannot read or create in the built warehouses.

The main database tests need `make build-prod` (and `make build` for the dev_marts check); the eval database
tests need `make eval-db`. Every denied read is preceded by an admin-side check that the relation exists, so a
missing table cannot pass for a missing privilege.
"""

from __future__ import annotations

import json
import re
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
    "dim_campaigns",
    "dim_customers",
    "dim_date",
    "dim_products",
    "dim_promotions",
    "fct_ad_spend_daily",
    "fct_order_discounts",
    "fct_order_items",
    "fct_orders",
    "fct_refunds",
    "fct_returns",
    "fct_sessions",
)
# Spec §6.5: the tables behind the masked views; no reader role can read them.
UNMASKED = ("dim_customers_unmasked",)
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

    assert [name for name in built if not name.endswith("_unmasked")] == sorted(MARTS)
    assert [name for name in built if name.endswith("_unmasked")] == sorted(UNMASKED)


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


class TestMaskedMarts:
    def test_no_reader_privilege_on_an_unmasked_relation_or_the_salt(
        self, admin: psycopg.Connection, reader: psycopg.Connection
    ) -> None:
        hidden = _hidden_relations(admin)
        assert {"marts.dim_customers_unmasked", "ops.pii_salt"} <= {name for _, name in hidden}
        role = _current_user(reader)
        query = sql.SQL(
            "select"
            " has_table_privilege(%(role)s, %(oid)s::oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),"
            " has_any_column_privilege(%(role)s, %(oid)s::oid, 'SELECT,INSERT,UPDATE,REFERENCES')"
        )

        granted = {
            (name, grantee): admin.execute(query, {"role": grantee, "oid": oid}).fetchone()
            for oid, name in hidden
            for grantee in (role, "public")
        }

        assert all(privileges == (False, False) for privileges in granted.values()), granted
        for mart in MARTS:
            row = admin.execute("select has_table_privilege(%s, %s, 'SELECT')", (role, f"marts.{mart}")).fetchone()
            assert row == (True,), mart

    @pytest.mark.parametrize(("schema", "table"), [("marts", "dim_customers_unmasked"), ("ops", "pii_salt")])
    def test_reading_an_unmasked_table_or_the_salt_is_denied(
        self, admin: psycopg.Connection, reader: psycopg.Connection, schema: str, table: str
    ) -> None:
        assert _exists(admin, schema, table)
        query = sql.SQL("select count(*) from {}").format(sql.Identifier(schema, table))

        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute(query)

    def test_pg_stats_shows_readers_no_personal_column(self, reader: psycopg.Connection) -> None:
        # Views have no statistics and the readers hold no privilege on a table with these columns.
        query = "select count(*) from pg_stats where attname in ('email', 'first_name', 'last_name')"

        row = reader.execute(query).fetchone()

        assert row == (0,)

    def test_pii_columns_are_md5_of_the_value_and_the_salt(
        self, admin: psycopg.Connection, reader: psycopg.Connection
    ) -> None:
        deleted = admin.execute(
            "select customer_id from marts.dim_customers_unmasked where is_deleted order by customer_id limit 1"
        ).fetchone()
        assert deleted is not None
        rows = reader.execute(
            "(select customer_id, email from marts.dim_customers where customer_id = %(id)s)"
            " union all"
            " (select customer_id, email from marts.dim_customers"
            " where not is_deleted order by customer_id limit 20)",
            {"id": deleted[0]},
        ).fetchall()
        assert len(rows) == 21
        assert any(row[0] == deleted[0] for row in rows)
        values = [value for row in rows for value in row[1:] if value is not None]
        assert values
        assert all(re.fullmatch("[0-9a-f]{32}", value) for value in values)

        row = admin.execute(
            "select bool_and(v.email is not distinct from md5(t.email || s.salt))"
            " from unnest(%s::bigint[], %s::text[]) as v(customer_id, email)"
            " left join marts.dim_customers_unmasked t on t.customer_id = v.customer_id"
            " cross join ops.pii_salt s",
            [[r[0] for r in rows], [r[1] for r in rows]],
        ).fetchone()

        assert row == (True,)

    def test_the_view_publishes_no_customer_name(self, reader: psycopg.Connection) -> None:
        row = reader.execute(
            "select count(*) from information_schema.columns"
            " where table_schema = 'marts' and table_name = 'dim_customers'"
            " and column_name in ('first_name', 'last_name')"
        ).fetchone()

        assert row == (0,)

    def test_counts_survive_the_hashing(self, admin: psycopg.Connection, reader: psycopg.Connection) -> None:
        query = "select count(email), count(distinct email) from {}"

        as_reader = reader.execute(sql.SQL(query).format(sql.Identifier("marts", "dim_customers"))).fetchone()
        as_admin = admin.execute(sql.SQL(query).format(sql.Identifier("marts", "dim_customers_unmasked"))).fetchone()

        assert as_reader == as_admin
        assert as_reader is not None
        assert as_reader[0] > 0

    @pytest.mark.parametrize(
        "query",
        [
            "select email as e from marts.dim_customers where customer_id = %s",
            "select lower(email) || '' from marts.dim_customers where customer_id = %s",
            "select c::text from marts.dim_customers c where customer_id = %s",
            "select to_jsonb(c)::text from marts.dim_customers c where customer_id = %s",
            "select (c.*)::text from marts.dim_customers c where customer_id = %s",
            "select c.row_to_json::text from marts.dim_customers c where customer_id = %s",
        ],
    )
    def test_whole_row_forms_return_only_hashes(
        self, admin: psycopg.Connection, reader: psycopg.Connection, query: str
    ) -> None:
        customer = admin.execute(
            "select customer_id from marts.dim_customers_unmasked"
            " where email is not null and first_name is not null and last_name is not null order by customer_id limit 1"
        ).fetchone()
        assert customer is not None
        row = reader.execute(query, (customer[0],)).fetchone()  # type: ignore[call-overload]
        assert row is not None

        leaked = admin.execute(
            "select position(t.email in %(out)s) > 0 or position(t.first_name in %(out)s) > 0"
            " or position(t.last_name in %(out)s) > 0"
            " from marts.dim_customers_unmasked t where customer_id = %(id)s",
            {"out": row[0], "id": customer[0]},
        ).fetchone()

        assert leaked is not None
        assert leaked[0] is False, f"{query} returned a real value"

    def test_the_salt_is_in_no_artifact(self, admin: psycopg.Connection) -> None:
        row = admin.execute("select salt from ops.pii_salt").fetchone()
        assert row is not None
        salt = row[0].encode()
        dbt_dir = Path(__file__).parents[2] / "dbt"
        folders = [*dbt_dir.glob("target*"), dbt_dir / "logs"]

        hits = [
            str(path)
            for folder in folders
            for path in folder.rglob("*")
            if path.is_file() and salt in path.read_bytes()
        ]

        assert hits == []


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


def _hidden_relations(admin: psycopg.Connection) -> list[tuple[int, str]]:
    """The relations no reader may touch, in every schema: any `*_unmasked` one and the salt."""
    rows = admin.execute(
        "select c.oid::int8, n.nspname || '.' || c.relname"
        " from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where c.relname like '%\\_unmasked' or (n.nspname = 'ops' and c.relname = 'pii_salt')"
    ).fetchall()
    return [(oid, name) for oid, name in rows]


def _current_user(conn: psycopg.Connection) -> str:
    row = conn.execute("select current_user").fetchone()
    assert row is not None
    return row[0]


def _exists(admin: psycopg.Connection, schema: str, table: str) -> bool:
    name = sql.Identifier(schema, table).as_string(admin)
    row = admin.execute("select to_regclass(%s) is not null", (name,)).fetchone()
    return row == (True,)
