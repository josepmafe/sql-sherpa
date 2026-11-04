"""Command line of the eval harness: `python -m sherpa_evals.run --check-gold [--case <id>]`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

import typer
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.server import EXAMPLES_PATH

from sherpa_evals.cases import GoldSet
from sherpa_evals.settings import CASES_PATH, EvalSettings
from sherpa_evals.warehouse import (  # CheckRow is in an annotation
    CheckRow,
    EvalDatabase,
    check_gold,
    selector_event_loop,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

app = typer.Typer(help="SQL Sherpa eval harness (agent spec §7).", add_completion=False)

TABLE_HEADER = "| case | problems | rows | closest example | score |\n| --- | --- | --- | --- | --- |"


@app.command()
def main(
    check_gold_option: Annotated[
        bool, typer.Option("--check-gold", help="Check every gold case against the eval database.")
    ] = False,
    case: Annotated[str | None, typer.Option("--case", help="Only this case id.")] = None,
) -> None:
    if not check_gold_option:
        raise typer.BadParameter("pass --check-gold", param_hint="--check-gold")

    rows = asyncio.run(_check_gold(case), loop_factory=selector_event_loop)
    typer.echo(render_table(rows))
    if has_problems(rows):
        raise typer.Exit(code=1)


def has_problems(rows: Sequence[CheckRow]) -> bool:
    return any(row.problems for row in rows)


def render_table(rows: Sequence[CheckRow]) -> str:
    lines = [TABLE_HEADER]
    for row in rows:
        problems = "; ".join(row.problems) or "-"
        count = "-" if row.rows is None else str(row.rows)
        cells = (row.case_id, problems, count, row.closest_example or "-", f"{row.closest_score:.2f}")
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    return "\n".join(lines)


async def _check_gold(case_id: str | None) -> tuple[CheckRow, ...]:
    settings = EvalSettings()  # type: ignore[call-arg]  # fields come from the environment / .env
    try:
        gold = GoldSet.load(CASES_PATH)
    except FileNotFoundError as error:
        raise typer.BadParameter(f"no gold set at {CASES_PATH}") from error

    if case_id is not None:
        selected = tuple(c for c in gold.cases if c.id == case_id)
        if not selected:
            raise typer.BadParameter(f"no case {case_id!r}", param_hint="--case")
        gold = gold.model_copy(update={"cases": selected})

    visible = MartCatalogLoader(settings.eval_target_path).load().visible_marts()
    examples = ExampleIndex.from_yaml(EXAMPLES_PATH)
    return await check_gold(gold, EvalDatabase(settings), visible, examples)


if __name__ == "__main__":
    app()
