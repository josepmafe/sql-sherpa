from __future__ import annotations

import os
import socket
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import psycopg
import pytest
from helpers import RUN_SCALE, RUN_SEED, RUN_TODAY, STATE_TABLE, assert_empty, fingerprint
from sherpa_generator import settings
from sherpa_generator.cli import app
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.simulation.simulator import Simulator
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

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


@pytest.fixture
def unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points the CLI's GeneratorSettings at a local port that was just free, so the connection fails fast."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]

    monkeypatch.setenv("SHERPA_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("SHERPA_DB_PORT", str(free_port))
    monkeypatch.setenv("SHERPA_DB_NAME", "sherpa")
    monkeypatch.setenv("SHERPA_ADMIN_USER", "sherpa_admin")
    monkeypatch.setenv("SHERPA_ADMIN_PASSWORD", "unused")
    # On Windows a refused port still waits out the connect timeout; libpq raises 1 to its 2 s minimum.
    monkeypatch.setattr(settings, "CONNECT_TIMEOUT_SECONDS", 1)


@pytest.fixture
def no_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No SHERPA_* variables and no .env in the working directory."""
    for name in list(os.environ):
        if name.startswith("SHERPA_"):
            monkeypatch.delenv(name)

    monkeypatch.chdir(tmp_path)


COMMANDS = [["generate", "--days", "1"], ["status"], ["start-date"], ["reset"]]


class TestCli:
    @pytest.mark.usefixtures("no_settings")
    @pytest.mark.parametrize("args", COMMANDS)
    def test_missing_settings_exit_2_with_message_not_traceback(self, args: list[str]) -> None:
        result = runner.invoke(app, args)

        assert result.exit_code == 2
        assert "error: invalid or missing settings" in result.stderr
        assert "SHERPA_DB_HOST: Field required" in result.stderr
        assert "check .env" in result.stderr
        assert "Traceback" not in result.stderr

    @pytest.mark.usefixtures("unreachable_database")
    def test_invalid_setting_names_the_variable_without_echoing_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_DB_PORT", "not-a-port")

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 2
        assert "SHERPA_DB_PORT: Input should be a valid integer" in result.stderr
        assert "unused" not in result.stderr
        assert "Traceback" not in result.stderr

    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("args", "target", "method"),
        [(["generate", "--days", "1"], Simulator, "run"), (["status"], RawSchema, "row_counts")],
    )
    def test_connection_lost_mid_command_exits_2_with_message_not_traceback(
        self, cli_database: Database, monkeypatch: pytest.MonkeyPatch, args: list[str], target: type, method: str
    ) -> None:
        def lose_connection(*_args: object, **_kwargs: object) -> None:
            raise psycopg.OperationalError("server closed the connection unexpectedly")

        monkeypatch.setattr(target, method, lose_connection)

        result = runner.invoke(app, args)

        assert result.exit_code == 2
        assert "server closed the connection unexpectedly" in result.stderr
        assert "make up" in result.stderr
        assert "Traceback" not in result.stderr

    @pytest.mark.usefixtures("unreachable_database")
    @pytest.mark.parametrize("args", COMMANDS)
    def test_unreachable_database_exits_2_with_message_not_traceback(self, args: list[str]) -> None:
        result = runner.invoke(app, args)

        assert result.exit_code == 2
        assert "error: cannot connect to the database" in result.stderr
        assert "Traceback" not in result.stderr

    def test_rejects_zero_days(self) -> None:
        result = runner.invoke(app, ["generate", "--days", "0"])

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
    def test_start_date_before_any_run_prints_nothing(self, cli_database: Database) -> None:
        result = runner.invoke(app, ["start-date"])

        assert result.exit_code == 0, result.output
        assert result.stdout == ""

    @pytest.mark.integration
    def test_start_date_prints_the_first_simulated_day(self, cli_database: Database) -> None:
        first = runner.invoke(app, ["generate", "--days", "1", "--start-date", "2025-03-01", "--scale", "0.1"])

        result = runner.invoke(app, ["start-date"])

        assert first.exit_code == 0, first.output
        assert result.exit_code == 0, result.output
        assert result.stdout == "2025-03-01\n"

    @pytest.mark.integration
    def test_reset_drops_and_recreates_raw_schemas(self, cli_database: Database) -> None:
        Simulator(cli_database, seed=RUN_SEED, scale=RUN_SCALE, today=RUN_TODAY).run(1, date(2025, 3, 1))
        tables_before = RawSchema(cli_database).tables()

        result = runner.invoke(app, ["reset"])

        assert result.exit_code == 0, result.output
        assert RawSchema(cli_database).tables() == tables_before
        assert_empty(cli_database)
