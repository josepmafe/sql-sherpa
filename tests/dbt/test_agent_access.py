"""What the agent role can and cannot read or create in the built warehouses, and the trigram indexes it searches with.

The main database tests need `make build-prod` (and `make build` for the dev_marts check); the eval database
tests need `make eval-db`. Every denied read is preceded by an admin-side check that the relation exists, so a
missing table cannot pass for a missing privilege.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterator

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
)
NOT_EXPOSED = (
    ("staging", "stg_shop__orders"),
    ("intermediate", "int_order_items__discount_allocated"),
    ("seeds", "countries"),
    ("raw_shop", "orders"),
)
TRIGRAM_INDEXES = [
    "dim_products_brand_trgm",
    "dim_products_category_l1_trgm",
    "dim_products_category_l2_trgm",
    "dim_products_category_l3_trgm",
    "dim_products_product_name_trgm",
]

pytestmark = pytest.mark.warehouse

Database = Literal["main", "eval"]
Role = Literal["admin", "agent"]


class WarehouseSettings(BaseSettings):
    """The admin and agent connections, read from `.env` like the generator's admin settings."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    eval_db_name: str = "sherpa_eval"
    admin_user: str
    admin_password: SecretStr
    agent_user: str
    agent_password: SecretStr

    def conninfo(self, database: Database, role: Role) -> str:
        user, password = (
            (self.admin_user, self.admin_password) if role == "admin" else (self.agent_user, self.agent_password)
        )
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name if database == "main" else self.eval_db_name,
            user=user,
            password=password.get_secret_value(),
        )


@pytest.fixture(params=["main", "eval"])
def database(request: pytest.FixtureRequest) -> Database:
    return request.param


@pytest.fixture
def agent(database: Database) -> Iterator[psycopg.Connection]:
    # autocommit: a denied query must not abort the session for the next statement.
    with psycopg.connect(WarehouseSettings().conninfo(database, "agent"), autocommit=True) as conn:
        yield conn


@pytest.fixture
def admin(database: Database) -> Iterator[psycopg.Connection]:
    with psycopg.connect(WarehouseSettings().conninfo(database, "admin"), autocommit=True) as conn:
        yield conn


class TestAgentAccess:
    @pytest.mark.parametrize("table", MARTS)
    def test_reads_every_mart(self, agent: psycopg.Connection, table: str) -> None:
        query = sql.SQL("select count(*) from marts.{}").format(sql.Identifier(table))

        row = agent.execute(query).fetchone()

        assert row is not None
        assert row[0] > 0

    @pytest.mark.parametrize(("schema", "table"), NOT_EXPOSED)
    def test_cannot_read_staging_intermediate_seeds_or_raw(
        self, admin: psycopg.Connection, agent: psycopg.Connection, schema: str, table: str
    ) -> None:
        assert _exists(admin, schema, table)
        query = sql.SQL("select count(*) from {}").format(sql.Identifier(schema, table))

        with pytest.raises(errors.InsufficientPrivilege):
            agent.execute(query)

    @pytest.mark.parametrize("database", ["main"], indirect=True)
    def test_cannot_read_dev_marts(self, admin: psycopg.Connection, agent: psycopg.Connection) -> None:
        # dbt's grants config gives the agent SELECT on dev_marts tables too; only the missing schema USAGE blocks it.
        assert _exists(admin, "dev_marts", "fct_orders")
        privileges = admin.execute(
            "select has_table_privilege(%(role)s, 'dev_marts.fct_orders', 'SELECT'),"
            " has_schema_privilege(%(role)s, 'dev_marts', 'USAGE')",
            {"role": WarehouseSettings().agent_user},
        ).fetchone()
        assert privileges == (True, False)
        query = "select count(*) from dev_marts.fct_orders"

        with pytest.raises(errors.InsufficientPrivilege):
            agent.execute(query)

    def test_has_no_usage_on_public(self, agent: psycopg.Connection) -> None:
        query = "select has_schema_privilege('public', 'USAGE')"

        row = agent.execute(query).fetchone()

        assert row == (False,)

    def test_cannot_create_temporary_tables_or_large_objects(self, admin: psycopg.Connection) -> None:
        # Both are writable storage outside the schema privileges; the init script and eval-db revoke them.
        query = (
            "select has_database_privilege(%(role)s, current_database(), 'TEMPORARY'),"
            " has_function_privilege(%(role)s, 'lo_from_bytea(oid, bytea)', 'EXECUTE'),"
            " has_function_privilege(%(role)s, 'lo_create(oid)', 'EXECUTE')"
        )

        privileges = admin.execute(query, {"role": WarehouseSettings().agent_user}).fetchone()

        assert privileges == (False, False, False)

    def test_can_use_trigram_similarity_unqualified(self, agent: psycopg.Connection) -> None:
        query = "select similarity('running', 'runing') > 0.3"

        row = agent.execute(query).fetchone()

        assert row == (True,)

    def test_reads_persisted_comments(self, agent: psycopg.Connection) -> None:
        query = "select obj_description('marts.fct_orders'::regclass)"

        row = agent.execute(query).fetchone()

        assert row is not None
        assert row[0] is not None


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


def _exists(admin: psycopg.Connection, schema: str, table: str) -> bool:
    name = sql.Identifier(schema, table).as_string(admin)
    row = admin.execute("select to_regclass(%s) is not null", (name,)).fetchone()
    return row == (True,)
