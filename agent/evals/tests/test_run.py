from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
from sherpa_evals.answerers import Attempt, AttemptStats
from sherpa_evals.compare import Result
from sherpa_evals.run import app, run_cases
from sherpa_evals.warehouse import EvalDatabase, EvalSqlError
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.cases import GoldCase

STATS = AttemptStats(latency_ms=5, usage=(), tool_calls=2, sql_attempts=1, trace_id="t-1")
GOLD_SQL = "select count(*) as n from marts.fct_orders"
AGENT_SQL = "select count(*) as total from marts.fct_orders"
BAD_SQL = "select nope from marts.fct_orders"
ROW_5 = Result(columns=("n",), rows=((5,),))
ROW_6 = Result(columns=("n",), rows=((6,),))


class StubAnswerer:
    """Answers each question with the attempt keyed by it."""

    name = "stub"

    def __init__(self, attempts: dict[str, Attempt]) -> None:
        self._attempts = attempts

    async def attempt(self, question: str) -> Attempt:
        return self._attempts[question]


def answered(sql: str) -> Attempt:
    return Attempt(outcome="answered", sql=sql, reason=None, stats=STATS)


def refusal(outcome: str, reason: str | None = None) -> Attempt:
    return Attempt(outcome=outcome, sql=None, reason=reason, stats=STATS)  # type: ignore[arg-type]


def fake_db(results: dict[str, Result | EvalSqlError]) -> EvalDatabase:
    db = create_autospec(EvalDatabase, instance=True)

    async def execute(sql: str) -> Result:
        item = results[sql]
        if isinstance(item, EvalSqlError):
            raise item
        return item

    db.execute.side_effect = execute
    return db


@pytest.mark.anyio
class TestRunCases:
    async def test_matching_answered_case_passes(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: answered(AGENT_SQL)})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_5}))

        assert len(records) == 1
        assert records[0].passed
        assert records[0].diff is None
        assert records[0].agent_sql == AGENT_SQL
        assert records[0].trace_id == "t-1"
        assert records[0].latency_ms == 5

    async def test_mismatching_answer_fails_with_the_comparison_diff(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: answered(AGENT_SQL)})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_6}))

        assert not records[0].passed
        assert records[0].diff == "no agent column matches gold column n"

    async def test_clarification_against_an_answered_case_fails(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: refusal("needs_clarification", "which year?")})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5}))

        assert not records[0].passed
        assert records[0].outcome == "needs_clarification"
        assert records[0].diff == "expected answered, got needs_clarification: which year?"
        assert records[0].schema_recall is None

    async def test_unanswerable_met_with_unanswerable_passes(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("unanswerable-001", expected_outcome="unanswerable", gold_sql=None, tables=())
        answerer = StubAnswerer({case.question: refusal("unanswerable")})

        records = await run_cases([case], answerer, fake_db({}))

        assert records[0].passed

    async def test_unanswerable_case_answered_fails(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("unanswerable-001", expected_outcome="unanswerable", gold_sql=None, tables=())
        answerer = StubAnswerer({case.question: answered(AGENT_SQL)})

        records = await run_cases([case], answerer, fake_db({}))

        assert not records[0].passed

    async def test_a_failing_case_does_not_stop_the_run(self, make_case: Callable[..., GoldCase]) -> None:
        broken = make_case("values-001", gold_sql=GOLD_SQL)
        fine = make_case("values-002", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({broken.question: answered(BAD_SQL), fine.question: answered(AGENT_SQL)})
        db = fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_5, BAD_SQL: EvalSqlError('column "nope" does not exist')})

        records = await run_cases([broken, fine], answerer, db)

        assert [r.passed for r in records] == [False, True]
        assert records[0].diff == 'agent SQL failed: column "nope" does not exist'

    async def test_repeat_gives_a_record_per_repeat(self, make_case: Callable[..., GoldCase]) -> None:
        cases = [make_case("values-001", gold_sql=GOLD_SQL), make_case("values-002", gold_sql=GOLD_SQL)]
        answerer = StubAnswerer({c.question: answered(AGENT_SQL) for c in cases})
        db = fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_5})

        records = await run_cases(cases, answerer, db, repeat=2)

        assert sorted((r.case_id, r.repeat) for r in records) == [
            ("values-001", 1),
            ("values-001", 2),
            ("values-002", 1),
            ("values-002", 2),
        ]

    async def test_gold_result_is_run_once_per_run(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        db = fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_5})

        await run_cases([case], StubAnswerer({case.question: answered(AGENT_SQL)}), db, repeat=3)

        executed = [call.args[0] for call in db.execute.call_args_list]
        assert executed.count(GOLD_SQL) == 1

    async def test_in_flight_attempts_never_exceed_the_concurrency(self, make_case: Callable[..., GoldCase]) -> None:
        cases = [make_case(f"values-{n:03d}", gold_sql=GOLD_SQL) for n in range(1, 9)]
        in_flight = 0
        peak = 0

        async def attempt(question: str) -> Attempt:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)  # lets the other tasks start; the peak is what is asserted
            in_flight -= 1
            return answered(AGENT_SQL)

        answerer = create_autospec(StubAnswerer, instance=True)
        answerer.attempt.side_effect = attempt

        await run_cases(cases, answerer, fake_db({GOLD_SQL: ROW_5, AGENT_SQL: ROW_5}), concurrency=3)

        assert peak == 3

    async def test_schema_recall_is_false_when_a_gold_table_is_missing(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        gold_sql = (
            "select count(*) as n from marts.fct_orders o join marts.dim_customers c on c.customer_id = o.customer_id"
        )
        case = make_case("values-001", gold_sql=gold_sql, tables=("dim_customers", "fct_orders"))
        answerer = StubAnswerer({case.question: answered(AGENT_SQL)})

        records = await run_cases([case], answerer, fake_db({gold_sql: ROW_5, AGENT_SQL: ROW_5}))

        assert records[0].schema_recall is False

    async def test_schema_recall_is_true_when_the_agent_reads_extra_tables(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        agent_sql = "select count(*) as n from marts.fct_orders o join marts.dim_customers c using (customer_id)"
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: answered(agent_sql)})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5, agent_sql: ROW_5}))

        assert records[0].schema_recall is True


class TestCli:
    def test_unknown_variant_is_refused(self) -> None:
        result = CliRunner().invoke(app, ["--variant", "no_such_variant"])

        assert result.exit_code == 2
