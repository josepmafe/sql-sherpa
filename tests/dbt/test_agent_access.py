"""What the agent role can and cannot read once the warehouse is built (`make build-prod`, then `make build`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

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

pytestmark = pytest.mark.warehouse


class AgentSettings(BaseSettings):
    """The agent role's connection, read from `.env` like the generator's admin settings."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    agent_user: str
    agent_password: SecretStr

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.agent_user,
            password=self.agent_password.get_secret_value(),
        )


@pytest.fixture
def agent() -> Iterator[psycopg.Connection]:
    # autocommit: a denied query must not abort the session for the next statement.
    with psycopg.connect(AgentSettings().conninfo(), autocommit=True) as conn:
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
        self, agent: psycopg.Connection, schema: str, table: str
    ) -> None:
        query = sql.SQL("select count(*) from {}").format(sql.Identifier(schema, table))

        with pytest.raises(errors.InsufficientPrivilege):
            agent.execute(query)

    def test_cannot_read_dev_marts(self, agent: psycopg.Connection) -> None:
        # dbt's grants config gives the agent SELECT on dev_marts tables too; only the missing schema USAGE blocks it.
        query = "select count(*) from dev_marts.fct_orders"

        with pytest.raises(errors.InsufficientPrivilege):
            agent.execute(query)

    def test_can_use_trigram_similarity_unqualified(self, agent: psycopg.Connection) -> None:
        query = "select similarity('running', 'runing') > 0.3"

        row = agent.execute(query).fetchone()

        assert row == (True,)

    def test_reads_persisted_comments(self, agent: psycopg.Connection) -> None:
        query = "select obj_description('marts.fct_orders'::regclass)"

        row = agent.execute(query).fetchone()

        assert row is not None
        assert row[0] is not None
