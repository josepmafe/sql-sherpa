from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import RUN_SCALE, RUN_SEED, RUN_TODAY, STATE_TABLE, fingerprint
from sherpa_generator.cli import app
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.simulation.simulator import Simulator
from typer.testing import CliRunner

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

runner = CliRunner()


@pytest.fixture
def cli_database(temp_database: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    """Points the CLI's GeneratorSettings at the throwaway database."""
    with temp_database.connect() as conn:
        row = conn.execute("SELECT current_database()").fetchone()

    assert row is not None
    monkeypatch.setenv("SHERPA_DB_NAME", row[0])
    return temp_database


class TestCli:
    def test_rejects_zero_days(self) -> None:
        result = runner.invoke(app, ["generate", "--days", "0"])

        assert result.exit_code == 2
        assert "--days" in result.stderr

    def test_requires_days(self) -> None:
        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 2
        assert "--days" in result.stderr

    @pytest.mark.integration
    def test_generator_error_exits_2_with_message_on_stderr(self, cli_database: Database) -> None:
        result = runner.invoke(app, ["generate", "--days", "1", "--start-date", "2999-01-01"])

        assert result.exit_code == 2
        assert "start date 2999-01-01 is after yesterday" in result.stderr
        assert fingerprint(cli_database)[STATE_TABLE] is None

    @pytest.mark.integration
    def test_generate_then_rerun_reports_nothing_to_simulate(self, cli_database: Database) -> None:
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        args = ["generate", "--days", "1", "--start-date", yesterday, "--scale", "0.1"]
        first = runner.invoke(app, args)

        second = runner.invoke(app, ["generate", "--days", "1", "--scale", "0.1"])

        assert first.exit_code == 0, first.output
        assert f"simulated {yesterday} .. {yesterday}" in first.stdout
        assert second.exit_code == 0, second.output
        assert "nothing to simulate" in second.stdout

    @pytest.mark.integration
    def test_status_reports_range_and_order_count(self, cli_database: Database) -> None:
        Simulator(cli_database, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY).run(3, date(2025, 3, 1))
        orders, _ = fingerprint(cli_database)["raw_shop.orders"]

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0, result.output
        assert orders > 0
        assert "simulated 2025-03-01 .. 2025-03-03 (seed 7, scale 0.1)" in result.stdout
        assert f"raw_shop.orders: {orders}\n" in result.stdout

    @pytest.mark.integration
    def test_status_before_any_run(self, cli_database: Database) -> None:
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0, result.output
        assert "no days simulated yet" in result.stdout
        assert "raw_shop.orders: 0\n" in result.stdout

    @pytest.mark.integration
    def test_reset_drops_and_recreates_raw_schemas(self, cli_database: Database) -> None:
        Simulator(cli_database, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY).run(1, date(2025, 3, 1))
        tables_before = RawSchema(cli_database).tables()

        result = runner.invoke(app, ["reset"])

        assert result.exit_code == 0, result.output
        assert RawSchema(cli_database).tables() == tables_before
        after = fingerprint(cli_database)
        assert after.pop(STATE_TABLE) is None
        assert all(count == 0 for count, _ in after.values())
