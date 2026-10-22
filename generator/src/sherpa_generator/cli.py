import logging
from datetime import UTC, datetime
from typing import Annotated

import typer

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
    simulator = Simulator(_database(), seed=seed, scale=scale, today=datetime.now(UTC).date())
    try:
        result = simulator.run(days, start_date.date() if start_date else None)
    except GeneratorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(GENERATOR_ERROR_EXIT_CODE) from exc

    if result.first_day is None:
        typer.echo("nothing to simulate: already caught up to yesterday")
        return

    typer.echo(f"simulated {result.first_day} .. {result.last_day}: {sum(result.rows_written.values())} rows written")


@app.command()
def status() -> None:
    """Print the simulated range and the row count of every raw table."""
    db = _database()
    with db.connect() as conn:
        state = SimulationStateStore(conn).load()

    if state is None or state.last_simulated_date is None:
        typer.echo("no days simulated yet")
    else:
        typer.echo(
            f"simulated {state.start_date} .. {state.last_simulated_date} (seed {state.seed}, scale {state.scale})"
        )

    for table, count in RawSchema(db).row_counts().items():
        typer.echo(f"{table}: {count}")


@app.command()
def reset() -> None:
    """Drop and recreate the raw schemas, deleting all generated data and the simulation state."""
    schema = RawSchema(Database(GeneratorSettings()))
    schema.drop()
    schema.create()
    typer.echo("raw schemas reset")


def _database() -> Database:
    """The configured database, with the raw schemas created on first use."""
    db = Database(GeneratorSettings())
    RawSchema(db).create()
    return db
