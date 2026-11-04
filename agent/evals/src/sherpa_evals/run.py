"""Command line of the eval harness (agent spec §7.2).

`python -m sherpa_evals.run --variant oracle [--category c] [--case id] [--repeat 1] [--concurrency 4]`
and `python -m sherpa_evals.run --check-gold [--case id]`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import sqlglot
import typer
from sherpa_mcp.catalog import MARTS_SCHEMA, MartCatalogLoader
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.server import EXAMPLES_PATH
from sherpa_mcp.sql_guard import DIALECT
from sqlglot import exp
from sqlglot.errors import SqlglotError

from sherpa_evals.answerers import ANSWERERS
from sherpa_evals.cases import GoldSet
from sherpa_evals.compare import compare
from sherpa_evals.report import CaseRecord, Prices, RunMeta, previous_run, render_report, write_run
from sherpa_evals.settings import CASES_PATH, PRICES_PATH, RUNS_DIR, EvalSettings
from sherpa_evals.warehouse import (  # CheckRow is in an annotation
    CheckRow,
    EvalDatabase,
    EvalSqlError,
    check_gold,
    selector_event_loop,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_evals.answerers import Answerer, Attempt
    from sherpa_evals.cases import GoldCase
    from sherpa_evals.compare import Result

app = typer.Typer(help="SQL Sherpa eval harness (agent spec §7).", add_completion=False)

TABLE_HEADER = "| case | problems | rows | closest example | score |\n| --- | --- | --- | --- | --- |"
DEFAULT_CONCURRENCY = 4  # §7.2


@app.command()
def main(
    variant: Annotated[str | None, typer.Option("--variant", help="The answerer to evaluate.")] = None,
    category: Annotated[str | None, typer.Option("--category", help="Only this category.")] = None,
    case: Annotated[str | None, typer.Option("--case", help="Only this case id.")] = None,
    repeat: Annotated[int, typer.Option("--repeat", min=1, help="Attempts per case.")] = 1,
    concurrency: Annotated[
        int, typer.Option("--concurrency", min=1, help="Attempts in flight at once.")
    ] = DEFAULT_CONCURRENCY,
    check_gold_option: Annotated[
        bool, typer.Option("--check-gold", help="Check every gold case against the eval database.")
    ] = False,
) -> None:
    if check_gold_option:
        rows = asyncio.run(_check_gold(case), loop_factory=selector_event_loop)
        typer.echo(render_table(rows))
        if has_problems(rows):
            raise typer.Exit(code=1)
        return

    if variant is None or variant not in ANSWERERS:
        raise typer.BadParameter(f"pass --variant, one of {sorted(ANSWERERS)}", param_hint="--variant")

    typer.echo(asyncio.run(_evaluate(variant, category, case, repeat, concurrency), loop_factory=selector_event_loop))


async def run_cases(
    cases: Sequence[GoldCase],
    answerer: Answerer,
    db: EvalDatabase,
    *,
    repeat: int = 1,
    concurrency: int = DEFAULT_CONCURRENCY,
    prices: Prices | None = None,
) -> list[CaseRecord]:
    """Scores exactly the cases it is given; a case that fails never stops the run."""
    scorer = _CaseScorer(db, prices or Prices(models={}))
    in_flight = asyncio.Semaphore(concurrency)

    async def run_one(case: GoldCase, number: int) -> CaseRecord:
        async with in_flight:
            attempt = await answerer.attempt(case.question)
        return await scorer.record(case, number, attempt)

    return list(await asyncio.gather(*(run_one(c, n) for n in range(1, repeat + 1) for c in cases)))


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


class _CaseScorer:
    """Judges attempts against the gold result, which it runs once per case."""

    def __init__(self, db: EvalDatabase, prices: Prices) -> None:
        self._db = db
        self._prices = prices
        self._gold_results: dict[str, asyncio.Future[Result]] = {}

    async def record(self, case: GoldCase, number: int, attempt: Attempt) -> CaseRecord:
        passed, diff = await self._judge(case, attempt)
        usage = attempt.stats.usage
        recall = None
        if case.expected_outcome == "answered" and attempt.sql is not None:
            recall = set(case.tables) <= _marts_read(attempt.sql)
        return CaseRecord(
            case_id=case.id,
            category=case.category,
            repeat=number,
            expected_outcome=case.expected_outcome,
            outcome=attempt.outcome,
            passed=passed,
            diff=diff,
            agent_sql=attempt.sql,
            latency_ms=attempt.stats.latency_ms,
            cost_eur=self._prices.cost_eur(usage),
            unpriced_models=self._prices.unpriced(usage),
            tool_calls=attempt.stats.tool_calls,
            sql_attempts=attempt.stats.sql_attempts,
            schema_recall=recall,
            trace_id=attempt.stats.trace_id,
        )

    async def _judge(self, case: GoldCase, attempt: Attempt) -> tuple[bool, str | None]:
        if case.expected_outcome == "answered" and attempt.outcome == "answered":
            if attempt.sql is None:
                return False, "answered without SQL"

            try:
                agent = await self._db.execute(attempt.sql)
            except EvalSqlError as error:
                return False, f"agent SQL failed: {error}"

            try:
                gold = await self._gold_result(case)
            except EvalSqlError as error:
                return False, f"gold SQL failed: {error}"

            comparison = compare(gold, agent, ordered=case.ordered)
            return comparison.passed, comparison.diff

        passed = case.expected_outcome != "answered" and attempt.outcome == case.expected_outcome
        return passed, None if passed else f"expected {case.expected_outcome}, got {attempt.outcome}: {attempt.reason}"

    def _gold_result(self, case: GoldCase) -> asyncio.Future[Result]:
        if case.id not in self._gold_results:
            assert case.gold_sql is not None  # an answered case has gold_sql
            self._gold_results[case.id] = asyncio.ensure_future(self._db.execute(case.gold_sql))
        return self._gold_results[case.id]


def _marts_read(sql: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return set()
    return {table.name for table in tree.find_all(exp.Table) if table.db == MARTS_SCHEMA}


def _load_gold() -> GoldSet:
    try:
        return GoldSet.load(CASES_PATH)
    except FileNotFoundError as error:
        raise typer.BadParameter(f"no gold set at {CASES_PATH}") from error


async def _evaluate(variant: str, category: str | None, case_id: str | None, repeat: int, concurrency: int) -> str:
    settings = EvalSettings()  # type: ignore[call-arg]  # fields come from the environment / .env
    gold = _load_gold()
    cases = [c for c in gold.accepted() if category in (None, c.category) and case_id in (None, c.id)]
    if not cases:
        raise typer.BadParameter("no accepted gold case matches")

    meta = RunMeta(
        variant=variant,
        gold_version=gold.gold_version,
        dataset_version=gold.dataset_version,
        started_at=datetime.now(UTC),
        repeat=repeat,
    )
    answerer = ANSWERERS[variant](gold, settings)
    records = await run_cases(
        cases,
        answerer,
        EvalDatabase(settings),
        repeat=repeat,
        concurrency=concurrency,
        prices=Prices.load(PRICES_PATH),
    )
    earlier = previous_run(RUNS_DIR, settings.default_variant, gold.gold_version, gold.dataset_version)
    report = render_report(
        meta, records, earlier[1] if earlier else None, earlier[0] if earlier else None, settings.default_variant
    )
    return f"{report}\nWritten to {write_run(RUNS_DIR, meta, records, report)}"


async def _check_gold(case_id: str | None) -> tuple[CheckRow, ...]:
    settings = EvalSettings()  # type: ignore[call-arg]  # fields come from the environment / .env
    gold = _load_gold()
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
