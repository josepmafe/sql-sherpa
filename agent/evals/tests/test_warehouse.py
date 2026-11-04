from __future__ import annotations

from unittest.mock import AsyncMock

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sherpa_evals.settings import EvalSettings
from sherpa_evals.warehouse import EvalDatabase, EvalSqlError


def make_settings() -> EvalSettings:
    return EvalSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.mark.anyio
class TestConnections:
    async def test_agent_session_is_read_only_with_a_30s_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect = AsyncMock(side_effect=psycopg.OperationalError("refused"))
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
        settings = make_settings()

        with pytest.raises(EvalSqlError):
            await EvalDatabase(settings).execute("select 1")

        info = conninfo_to_dict(connect.call_args.args[0])
        assert info["options"] == "-c default_transaction_read_only=on -c statement_timeout=30s"
        assert info["dbname"] == "sherpa_eval"
        assert info["user"] == settings.agent_user

    async def test_admin_connects_as_the_admin_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect = AsyncMock(side_effect=psycopg.OperationalError("refused"))
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
        settings = make_settings()

        with pytest.raises(EvalSqlError):
            await EvalDatabase(settings).execute_admin("select 1")

        info = conninfo_to_dict(connect.call_args.args[0])
        assert info["user"] == settings.admin_user
        assert info["options"] == "-c default_transaction_read_only=on -c statement_timeout=30s"

    async def test_unreachable_database_is_an_eval_sql_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(side_effect=psycopg.OperationalError("down")))

        with pytest.raises(EvalSqlError, match="down"):
            await EvalDatabase(make_settings()).execute("select 1")


@pytest.mark.warehouse
@pytest.mark.anyio
class TestEvalDatabase:
    async def test_statement_timeout_is_30s(self) -> None:
        result = await EvalDatabase(make_settings()).execute("select current_setting('statement_timeout')")

        assert result.rows == (("30s",),)

    async def test_execute_returns_every_row_without_a_cap(self) -> None:
        result = await EvalDatabase(make_settings()).execute("select order_id from marts.fct_orders")

        assert len(result.rows) > 101
        assert result.columns == ("order_id",)

    async def test_session_is_read_only(self) -> None:
        with pytest.raises(EvalSqlError, match="cannot execute CREATE TABLE in a read-only transaction"):
            await EvalDatabase(make_settings()).execute("create table marts.eval_probe (i int)")

    async def test_admin_reads_raw_shop(self) -> None:
        result = await EvalDatabase(make_settings()).execute_admin("select count(*) as n from raw_shop.orders")

        assert result.rows[0][0] > 0

    async def test_admin_session_is_read_only(self) -> None:
        with pytest.raises(EvalSqlError, match="read-only transaction"):
            await EvalDatabase(make_settings()).execute_admin("create table marts.eval_probe (i int)")

    async def test_agent_cannot_read_unmasked_pii(self) -> None:
        with pytest.raises(EvalSqlError, match="permission denied"):
            await EvalDatabase(make_settings()).execute("select * from marts.dim_customers_unmasked")
