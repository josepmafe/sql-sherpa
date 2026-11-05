from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
import typer
import yaml
from sherpa_evals import run
from sherpa_evals.answerers import Attempt, AttemptStats, OracleAnswerer
from sherpa_evals.cases import GoldSet
from sherpa_evals.compare import Result
from sherpa_evals.run import app, run_cases
from sherpa_evals.settings import CASES_PATH, EvalSettings
from sherpa_evals.warehouse import EvalDatabase, EvalSqlError
from sherpa_mcp.catalog import CatalogUnavailable, MartCatalogLoader
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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

    async def test_answered_without_sql_fails_with_its_own_diff(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: refusal("answered")})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5}))

        assert not records[0].passed
        assert records[0].diff == "answered without SQL"

    async def test_a_failing_gold_query_fails_its_cases_and_not_the_run(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        broken = make_case("values-001", gold_sql=BAD_SQL)
        fine = make_case("values-002", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({broken.question: answered(AGENT_SQL), fine.question: answered(AGENT_SQL)})
        db = fake_db({BAD_SQL: EvalSqlError("gold is broken"), GOLD_SQL: ROW_5, AGENT_SQL: ROW_5})

        records = await run_cases([broken, fine], answerer, db, repeat=2)

        by_case = {(r.case_id, r.repeat): r for r in records}
        assert [by_case[("values-001", n)].diff for n in (1, 2)] == ["gold SQL failed: gold is broken"] * 2
        assert by_case[("values-002", 1)].passed
        assert by_case[("values-002", 2)].passed

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

    async def test_simultaneous_database_calls_never_exceed_the_concurrency(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        # The sherpa_agent role allows 20 connections and every execute opens one.
        cases = [make_case(f"values-{n:03d}", gold_sql=f"{GOLD_SQL} -- {n}") for n in range(1, 35)]
        answerer = StubAnswerer({c.question: answered(AGENT_SQL) for c in cases})
        calls = 0
        peak = 0
        db = create_autospec(EvalDatabase, instance=True)

        async def execute(sql: str) -> Result:
            nonlocal calls, peak
            calls += 1
            peak = max(peak, calls)
            await asyncio.sleep(0.005)  # lets the other cases reach their database calls; the peak is asserted
            calls -= 1
            return ROW_5

        db.execute.side_effect = execute

        records = await run_cases(cases, answerer, db, concurrency=4)

        assert all(r.passed for r in records)
        assert peak == 4

    async def test_a_failed_gold_query_is_retried_by_the_next_repeat(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql=GOLD_SQL)
        gold_calls = 0
        db = create_autospec(EvalDatabase, instance=True)

        async def execute(sql: str) -> Result:
            nonlocal gold_calls
            if sql != GOLD_SQL:
                return ROW_5
            gold_calls += 1
            if gold_calls == 1:
                raise EvalSqlError("connection reset")
            return ROW_5

        db.execute.side_effect = execute

        answerer = StubAnswerer({case.question: answered(AGENT_SQL)})
        records = await run_cases([case], answerer, db, repeat=2, concurrency=1)

        assert [(r.repeat, r.passed) for r in records] == [(1, False), (2, True)]
        assert records[0].diff == "gold SQL failed: connection reset"

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

    async def test_schema_recall_ignores_the_case_of_the_agent_table_names(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        agent_sql = "select count(*) as n from MARTS.FCT_ORDERS"
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: answered(agent_sql)})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5, agent_sql: ROW_5}))

        assert records[0].schema_recall is True

    async def test_schema_recall_is_true_when_the_agent_reads_extra_tables(
        self, make_case: Callable[..., GoldCase]
    ) -> None:
        agent_sql = "select count(*) as n from marts.fct_orders o join marts.dim_customers c using (customer_id)"
        case = make_case("values-001", gold_sql=GOLD_SQL)
        answerer = StubAnswerer({case.question: answered(agent_sql)})

        records = await run_cases([case], answerer, fake_db({GOLD_SQL: ROW_5, agent_sql: ROW_5}))

        assert records[0].schema_recall is True


ACCEPTED_UNANSWERABLE = {
    "id": "unanswerable-001",
    "category": "unanswerable",
    "question": "Who?",
    "tranche": 1,
    "expected_outcome": "unanswerable",
    "gold_sql": None,
    "rationale": "r",
    "status": "accepted",
}


class CountingAnswerer:
    name = "counting"

    def __init__(self) -> None:
        self.attempts = 0

    async def attempt(self, question: str) -> Attempt:
        self.attempts += 1
        return refusal("unanswerable")


def point_run_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: EvalDatabase | None = None) -> CountingAnswerer:
    """An accepted-case file and a runs directory under tmp_path; returns the answerer used for 'oracle'."""
    cases_path = tmp_path / "cases.yaml"
    gold = {"gold_version": 1, "dataset_version": 1, "cases": [ACCEPTED_UNANSWERABLE]}
    cases_path.write_text(yaml.safe_dump(gold), encoding="utf-8")
    database = db or fake_db({"select 1": ROW_5})
    monkeypatch.setattr(run, "CASES_PATH", cases_path)
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(run, "EvalDatabase", lambda settings: database)
    answerer = CountingAnswerer()
    monkeypatch.setitem(run.ANSWERERS, "oracle", lambda gold, settings: answerer)
    return answerer


@pytest.mark.anyio
class TestEvaluate:
    async def test_a_run_without_an_earlier_default_variant_run_names_the_default_variant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        point_run_at(tmp_path, monkeypatch)

        output = await run._evaluate("oracle", None, None, 1, 1)

        assert "No earlier run of v0_baseline with gold_version 1 and dataset_version 1." in output

    async def test_an_unreadable_earlier_run_fails_before_any_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answerer = point_run_at(tmp_path, monkeypatch)
        earlier = tmp_path / "runs" / "20250101T000000Z-v0_baseline"
        earlier.mkdir(parents=True)
        (earlier / "meta.json").write_text("{not json", encoding="utf-8")
        (earlier / "cases.jsonl").write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="JSON"):
            await run._evaluate("oracle", None, None, 1, 1)

        assert answerer.attempts == 0

    async def test_a_database_the_agent_role_cannot_use_fails_before_any_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = create_autospec(EvalDatabase, instance=True)
        db.execute.side_effect = EvalSqlError('database "sherpa_eval" does not exist')
        answerer = point_run_at(tmp_path, monkeypatch, db)

        with pytest.raises(typer.BadParameter, match="does not exist"):
            await run._evaluate("oracle", None, None, 1, 1)

        assert answerer.attempts == 0
        assert not (tmp_path / "runs").exists()


class TestCheckGoldCatalog:
    def test_a_missing_eval_catalog_names_the_fix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def unavailable(self: MartCatalogLoader) -> None:
            raise CatalogUnavailable("no manifest")

        monkeypatch.setattr(MartCatalogLoader, "load", unavailable)

        result = CliRunner().invoke(app, ["--check-gold"])

        assert result.exit_code == 2
        assert "make eval-db" in result.output
        assert "target-eval" in result.output
        assert "make build-prod" not in result.output


class TestCli:
    def test_unknown_variant_is_refused(self) -> None:
        result = CliRunner().invoke(app, ["--variant", "no_such_variant"])

        assert result.exit_code == 2

    @pytest.mark.parametrize(("concurrency", "exit_code"), [("13", 2), ("12", 1)])
    def test_concurrency_limit_is_12(self, monkeypatch: pytest.MonkeyPatch, concurrency: str, exit_code: int) -> None:
        async def fake_evaluate(*args: object) -> str:
            raise RuntimeError("past the option check")  # the CLI turns an unhandled error into exit code 1

        monkeypatch.setattr(run, "_evaluate", fake_evaluate)

        result = CliRunner().invoke(app, ["--variant", "oracle", "--concurrency", concurrency])

        assert result.exit_code == exit_code


@pytest.mark.warehouse
@pytest.mark.anyio
async def test_the_oracle_passes_every_gold_case_at_the_default_concurrency() -> None:
    gold = GoldSet.load(CASES_PATH)
    db = EvalDatabase(EvalSettings())  # type: ignore[call-arg]  # fields come from the environment / .env

    records = await run_cases(gold.cases, OracleAnswerer(gold), db)

    assert [(r.case_id, r.diff) for r in records if not r.passed] == []


@pytest.mark.warehouse
@pytest.mark.anyio
async def test_a_month_bucket_left_as_a_timestamp_matches_the_gold_date() -> None:
    gold = GoldSet.load(CASES_PATH)
    case = next(c for c in gold.cases if c.id == "time-004")
    assert case.gold_sql is not None
    assert "::date" in case.gold_sql
    uncast = case.gold_sql.replace("::date", "")
    db = EvalDatabase(EvalSettings())  # type: ignore[call-arg]  # fields come from the environment / .env
    answerer = StubAnswerer({case.question: answered(uncast)})

    records = await run_cases([case], answerer, db)

    assert [(r.case_id, r.diff) for r in records if not r.passed] == []
