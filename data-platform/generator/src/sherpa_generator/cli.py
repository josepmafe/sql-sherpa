import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

import psycopg
import typer
from pydantic import ValidationError

from sherpa_generator.database.connection import Database
from sherpa_generator.database.raw_schema import RawSchema
from sherpa_generator.settings import GeneratorSettings
from sherpa_generator.simulation.simulation_state import SimulationStateStore
from sherpa_generator.simulation.simulator import GeneratorError, Simulator

DEFAULT_SEED = 42
DEFAULT_SCALE = 1.0
GENERATOR_ERROR_EXIT_CODE = 2
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

app = typer.Typer(help="SQL Sherpa synthetic shop generator (Spec A §5).", no_args_is_help=True)


@app.callback()
def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)  # stderr, so stdout carries only the results


@app.command()
def generate(
    days: Annotated[int, typer.Option(min=1, help="Days to simulate, never beyond yesterday (UTC).")],
    start_date: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="First day; only on the first run.")
    ] = None,
    scale: Annotated[float, typer.Option(help="Multiplier for all daily volumes.")] = DEFAULT_SCALE,
    seed: Annotated[int, typer.Option(help="Seed for every random draw.")] = DEFAULT_SEED,
) -> None:
    """Simulate DAYS more days: the first run sets up the catalog, later runs resume after the last simulated day."""
    with _exit_on_error(), _connection_errors():
        simulator = Simulator(_database(), seed=seed, scale=scale, today=datetime.now(UTC).date())
        result = simulator.run(days, start_date.date() if start_date else None)

    if result.first_day is None:
        typer.echo("nothing to simulate: already caught up to yesterday")
        return

    typer.echo(f"simulated {result.first_day} .. {result.last_day}: {sum(result.rows_written.values())} rows written")


@app.command()
def status() -> None:
    """Print the simulated range and the row count of every raw table."""
    with _exit_on_error(), _connection_errors():
        db = _database()
        with db.connect() as conn:
            state = SimulationStateStore(conn).load()

        counts = RawSchema(db).row_counts()

    if state is None or state.last_simulated_date is None:
        typer.echo("no days simulated yet")
    else:
        typer.echo(
            f"simulated {state.start_date} .. {state.last_simulated_date} (seed {state.seed}, scale {state.scale})"
        )

    for table, count in counts.items():
        typer.echo(f"{table}: {count}")


@app.command()
def reset() -> None:
    """Drop and recreate the raw schemas, deleting all generated data and the simulation state."""
    with _exit_on_error(), _connection_errors():
        schema = RawSchema(Database(_settings()))
        schema.drop()
        schema.create()

    typer.echo("raw schemas reset")


@contextmanager
def _exit_on_error() -> Generator[None]:
    """A GeneratorError becomes a one-line message on stderr and exit code 2 instead of a traceback."""
    try:
        yield
    except GeneratorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(GENERATOR_ERROR_EXIT_CODE) from exc


@contextmanager
def _connection_errors() -> Generator[None]:
    """Postgres down or gone mid-command (connect or any later query) is a GeneratorError."""
    try:
        yield
    except psycopg.OperationalError as exc:
        msg = f"cannot connect to the database (is it running? see `make up`): {exc}"
        raise GeneratorError(msg) from exc


def _database() -> Database:
    """The configured database, with the raw schemas created on first use."""
    db = Database(_settings())
    RawSchema(db).create()
    return db


def _settings() -> GeneratorSettings:
    try:
        return GeneratorSettings()
    except ValidationError as exc:
        # Name the variables only: the error's input values would include the password.
        prefix = GeneratorSettings.model_config.get("env_prefix", "")
        problems = "; ".join(f"{prefix}{str(error['loc'][0]).upper()}: {error['msg']}" for error in exc.errors())
        msg = f"invalid or missing settings ({problems}); check .env (copy .env.example)"
        raise GeneratorError(msg) from exc
