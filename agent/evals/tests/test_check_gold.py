from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
from sherpa_evals import run
from sherpa_evals.cases import GoldSet
from sherpa_evals.compare import Result
from sherpa_evals.run import has_problems
from sherpa_evals.warehouse import CheckRow, EvalDatabase, EvalSqlError, check_gold
from sherpa_mcp.examples import Example, ExampleIndex
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.cases import GoldCase

VISIBLE = frozenset({"fct_orders", "dim_products"})
GOOD_SQL = "select count(*) as n from marts.fct_orders"
OTHER_SQL = "select count(*) as n from marts.dim_products"
RECONCILE_SQL = "select count(*) as n from raw_shop.orders"
ONE_ROW = Result(columns=("n",), rows=((5,),))
OTHER_ROW = Result(columns=("n",), rows=((6,),))
EMPTY = Result(columns=("n",), rows=())


@pytest.fixture
def examples() -> ExampleIndex:
    return ExampleIndex(
        [
            Example(
                question="How many orders were placed?", sql="select count(*) from marts.fct_orders", notes="orders"
            ),
            Example(question="Which products sell best?", sql="select product_id from marts.dim_products", notes="x"),
        ]
    )


def gold_set(*cases: GoldCase) -> GoldSet:
    return GoldSet(gold_version=1, dataset_version=1, cases=cases)


def fake_db(
    results: dict[str, list[Result | EvalSqlError]], admin: dict[str, Result | EvalSqlError] | None = None
) -> EvalDatabase:
    """`results[sql]` is consumed one entry per call; the last one repeats."""
    db = create_autospec(EvalDatabase, instance=True)

    async def execute(sql: str) -> Result:
        queue = results[sql]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, EvalSqlError):
            raise item
        return item

    async def execute_admin(sql: str) -> Result:
        item = (admin or {})[sql]
        if isinstance(item, EvalSqlError):
            raise item
        return item

    db.execute.side_effect = execute
    db.execute_admin.side_effect = execute_admin
    return db


@pytest.mark.anyio
class TestCheckGold:
    async def test_clean_answered_case_has_no_problems(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, question="How many orders do we have?")

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [ONE_ROW]}), VISIBLE, examples)

        assert rows[0].case_id == "values-001"
        assert rows[0].problems == ()
        assert rows[0].rows == 1
        assert rows[0].closest_example == "How many orders were placed?"
        assert rows[0].closest_score > 0

    async def test_result_differing_between_runs_is_not_deterministic(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL)

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [ONE_ROW, OTHER_ROW]}), VISIBLE, examples)

        assert len(rows[0].problems) == 1
        assert rows[0].problems[0].startswith("not deterministic: ")

    async def test_zero_rows_is_a_problem(self, make_case: Callable[..., GoldCase], examples: ExampleIndex) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL)

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [EMPTY]}), VISIBLE, examples)

        assert rows[0].problems == ("returns no rows",)
        assert rows[0].rows == 0

    async def test_zero_rows_with_allow_empty_is_fine(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, allow_empty=True)

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [EMPTY]}), VISIBLE, examples)

        assert rows[0].problems == ()

    async def test_disagreeing_reconcile_sql_is_a_problem(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, reconcile_sql=RECONCILE_SQL)
        db = fake_db({GOOD_SQL: [ONE_ROW]}, {RECONCILE_SQL: OTHER_ROW})

        rows = await check_gold(gold_set(case), db, VISIBLE, examples)

        assert len(rows[0].problems) == 1
        assert rows[0].problems[0].startswith("reconcile_sql disagrees: ")

    async def test_agreeing_reconcile_sql_is_fine(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, reconcile_sql=RECONCILE_SQL)
        db = fake_db({GOOD_SQL: [ONE_ROW]}, {RECONCILE_SQL: ONE_ROW})

        rows = await check_gold(gold_set(case), db, VISIBLE, examples)

        assert rows[0].problems == ()

    async def test_reconcile_sql_error_is_prefixed(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, reconcile_sql=RECONCILE_SQL)
        db = fake_db({GOOD_SQL: [ONE_ROW]}, {RECONCILE_SQL: EvalSqlError("relation does not exist")})

        rows = await check_gold(gold_set(case), db, VISIBLE, examples)

        assert rows[0].problems == ("reconcile_sql: relation does not exist",)

    async def test_sql_error_is_a_problem_and_the_next_case_is_still_checked(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        broken = make_case("values-001", gold_sql=GOOD_SQL)
        fine = make_case("values-002", gold_sql=OTHER_SQL, tables=("dim_products",))
        db = fake_db({GOOD_SQL: [EvalSqlError('relation "x" does not exist')], OTHER_SQL: [OTHER_ROW]})

        rows = await check_gold(gold_set(broken, fine), db, VISIBLE, examples)

        assert rows[0].problems == ('relation "x" does not exist',)
        assert rows[0].rows is None
        assert rows[1].problems == ()
        assert rows[1].rows == 1

    async def test_unanswerable_case_runs_no_sql_and_gets_its_closest_example(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case(
            "values-001",
            expected_outcome="unanswerable",
            gold_sql=None,
            tables=(),
            question="Which products sell best?",
        )
        db = fake_db({})

        rows = await check_gold(gold_set(case), db, VISIBLE, examples)

        db.execute.assert_not_called()
        db.execute_admin.assert_not_called()
        assert rows[0].rows is None
        assert rows[0].closest_example == "Which products sell best?"

    async def test_static_problems_are_reported(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, tables=("dim_products",))

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [ONE_ROW]}), VISIBLE, examples)

        assert len(rows[0].problems) == 1

    async def test_question_unlike_every_example_has_no_closest_example(
        self, make_case: Callable[..., GoldCase], examples: ExampleIndex
    ) -> None:
        case = make_case("values-001", gold_sql=GOOD_SQL, question="zzz qqq?")

        rows = await check_gold(gold_set(case), fake_db({GOOD_SQL: [ONE_ROW]}), VISIBLE, examples)

        assert rows[0].closest_example == ""
        assert rows[0].closest_score == 0.0


class TestCli:
    @pytest.mark.parametrize(("problems", "exit_code"), [(("returns no rows",), 1), ((), 0)])
    def test_exit_code_follows_problems(
        self, monkeypatch: pytest.MonkeyPatch, problems: tuple[str, ...], exit_code: int
    ) -> None:
        row = CheckRow(case_id="values-001", problems=problems, rows=0, closest_example="", closest_score=0.0)

        async def fake_check(case_id: str | None) -> tuple[CheckRow, ...]:
            return (row,)

        monkeypatch.setattr(run, "_check_gold", fake_check)

        result = CliRunner().invoke(run.app, ["--check-gold"])

        assert result.exit_code == exit_code
        assert "values-001" in result.output

    def test_without_check_gold_it_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(run.app, [])

        assert result.exit_code == 2


class TestHasProblems:
    def test_a_problem_means_exit_code_one(self) -> None:
        clean = CheckRow(case_id="a", problems=(), rows=1, closest_example="", closest_score=0.0)
        bad = CheckRow(case_id="b", problems=("x",), rows=1, closest_example="", closest_score=0.0)

        assert has_problems((clean,)) is False
        assert has_problems((clean, bad)) is True
