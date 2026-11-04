"""Access to the frozen eval database and the gold-set check (agent spec §7.1 step 2, §7.2)."""

from __future__ import annotations

import asyncio
import selectors
from typing import TYPE_CHECKING, Final

import psycopg
from psycopg import sql as pg_sql
from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, ConfigDict

from sherpa_evals.cases import static_problems
from sherpa_evals.compare import Result, compare

if TYPE_CHECKING:
    from sherpa_mcp.examples import ExampleIndex

    from sherpa_evals.cases import GoldCase, GoldSet
    from sherpa_evals.settings import EvalSettings

EVAL_STATEMENT_TIMEOUT: Final = "30s"  # a gold query may be slower than the agent's 5 s (§7.2)
CONNECT_TIMEOUT_SECONDS: Final = 10
SESSION_OPTIONS: Final = f"-c default_transaction_read_only=on -c statement_timeout={EVAL_STATEMENT_TIMEOUT}"
NO_ROWS: Final = "returns no rows"
NOT_DETERMINISTIC: Final = "not deterministic: {diff}"
RECONCILE_DISAGREES: Final = "reconcile_sql disagrees: {diff}"


class EvalSqlError(Exception):
    """A query the eval database refused or could not run; the message is Postgres's first error line."""


class CheckRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    problems: tuple[str, ...]
    rows: int | None
    closest_example: str
    closest_score: float


class EvalDatabase:
    """One new connection per call, closed after it; every session is read-only."""

    def __init__(self, settings: EvalSettings) -> None:
        self._settings = settings

    async def execute(self, sql: str) -> Result:
        """As the agent's role, with no row cap."""
        settings = self._settings
        return await self._run(sql, settings.agent_user, settings.agent_password.get_secret_value())

    async def execute_admin(self, sql: str) -> Result:
        """As `sherpa_admin`, which also reads the raw schemas (reconcile queries)."""
        settings = self._settings
        return await self._run(sql, settings.admin_user, settings.admin_password.get_secret_value())

    async def _run(self, sql: str, user: str, password: str) -> Result:
        conninfo = make_conninfo(
            host=self._settings.db_host,
            port=self._settings.db_port,
            dbname=self._settings.eval_db_name,
            user=user,
            password=password,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=SESSION_OPTIONS,
        )
        try:
            async with await psycopg.AsyncConnection.connect(conninfo) as conn:
                # The implicit transaction is rolled back on exit; nothing is ever committed.
                cursor = await conn.execute(pg_sql.SQL(sql))  # type: ignore[arg-type]  # runtime string, guarded
                columns = tuple(column.name for column in cursor.description or ())
                rows = tuple(tuple(row) for row in await cursor.fetchall())
        except psycopg.Error as error:
            message = error.diag.message_primary or str(error)
            raise EvalSqlError(message.splitlines()[0] if message else type(error).__name__) from error
        return Result(columns=columns, rows=rows)


def selector_event_loop() -> asyncio.AbstractEventLoop:
    """psycopg's async mode needs a selector loop, which Windows' default (Proactor) is not."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def check_gold(
    gold: GoldSet, db: EvalDatabase, visible_marts: frozenset[str], examples: ExampleIndex
) -> tuple[CheckRow, ...]:
    return tuple([await _check_case(case, db, visible_marts, examples) for case in gold.cases])


async def _check_case(
    case: GoldCase, db: EvalDatabase, visible_marts: frozenset[str], examples: ExampleIndex
) -> CheckRow:
    problems = static_problems(case, visible_marts)
    rows: int | None = None
    if case.expected_outcome == "answered" and case.gold_sql is not None:
        try:
            rows = await _check_results(case, case.gold_sql, db, problems)
        except EvalSqlError as error:
            problems.append(str(error))

    closest = examples.top(case.question, 1)
    return CheckRow(
        case_id=case.id,
        problems=tuple(problems),
        rows=rows,
        closest_example=closest[0].question if closest else "",
        closest_score=closest[0].score if closest else 0.0,
    )


async def _check_results(case: GoldCase, gold_sql: str, db: EvalDatabase, problems: list[str]) -> int:
    first = await db.execute(gold_sql)
    second = await db.execute(gold_sql)
    again = compare(first, second, ordered=case.ordered)
    if not again.passed:
        problems.append(NOT_DETERMINISTIC.format(diff=again.diff))
    if not first.rows and not case.allow_empty:
        problems.append(NO_ROWS)
    if case.reconcile_sql is not None:
        reconciled = compare(first, await db.execute_admin(case.reconcile_sql), ordered=case.ordered)
        if not reconciled.passed:
            problems.append(RECONCILE_DISAGREES.format(diff=reconciled.diff))
    return len(first.rows)
