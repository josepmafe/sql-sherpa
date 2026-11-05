"""The oracle proves a right answer passes; these wrong answerers prove a wrong one fails (agent spec §7.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from mutants import MUTATIONS, AlwaysAnsweredAnswerer, MutantAnswerer, changes, identical
from sherpa_evals.cases import GoldCase, GoldSet
from sherpa_evals.run import run_cases
from sherpa_evals.settings import CASES_PATH, EvalSettings
from sherpa_evals.warehouse import EvalDatabase, EvalSqlError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.answerers import Answerer

pytestmark = [pytest.mark.warehouse, pytest.mark.anyio]


def fixture_case(id: str, question: str, gold_sql: str, **overrides: object) -> GoldCase:
    category = id.rsplit("-", 1)[0]
    fields: dict[str, object] = {
        "id": id,
        "category": category,
        "question": question,
        "tranche": 1,
        "expected_outcome": "answered",
        "gold_sql": gold_sql,
        "tables": ("fct_orders",),
        "rationale": "Negative-control fixture.",
        "status": "accepted",
    }
    fields.update(overrides)
    return GoldCase(**fields)  # type: ignore[arg-type]


FIXTURE_CASES = (
    # A cancelled order's net_revenue is 0 (data platform spec 6.5), so dropping the filter leaves this result alone.
    fixture_case(
        "metrics-901",
        "What was the net revenue by sales channel in March 2025?",
        "select sales_channel, sum(net_revenue) as net_revenue from marts.fct_orders "
        "where not is_cancelled and order_date >= date '2025-03-01' and order_date < date '2025-04-01' "
        "group by sales_channel",
    ),
    fixture_case(
        "metrics-902",
        "How many orders and how much gross revenue did we have per month in Q1 2025, without cancelled orders?",
        "select cast(date_trunc('month', order_date) as date) as month, count(*) as orders, "
        "sum(gross_revenue) as gross_revenue from marts.fct_orders "
        "where not is_cancelled and order_date >= date '2025-01-01' and order_date < date '2025-04-01' "
        "group by 1",
    ),
    fixture_case(
        "complex-901",
        "Which are the top 5 products by net revenue in 2025?",
        "select product_id, sum(net_amount) as revenue from marts.fct_order_items "
        "where not is_cancelled and order_date >= date '2025-01-01' and order_date < date '2026-01-01' "
        "group by product_id order by sum(net_amount) desc, product_id limit 5",
        ordered=True,
        tables=("fct_order_items",),
    ),
    fixture_case(
        "unanswerable-901",
        "How many units of each product are in stock?",
        None,
        expected_outcome="unanswerable",
        tables=(),
    ),
)
FIXTURE_GOLD = GoldSet(gold_version=1, dataset_version=1, cases=FIXTURE_CASES)


@pytest.fixture(scope="module")
def db() -> EvalDatabase:
    return EvalDatabase(EvalSettings())  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture
def accepted_gold() -> GoldSet:
    if not CASES_PATH.exists():
        pytest.skip("no accepted gold cases yet")
    gold = GoldSet.load(CASES_PATH)
    if not gold.accepted():
        pytest.skip("no accepted gold cases yet")
    return GoldSet(gold_version=gold.gold_version, dataset_version=gold.dataset_version, cases=gold.accepted())


async def assert_pass_equals_identity(gold: GoldSet, mutation: Callable[[str], str], db: EvalDatabase) -> int:
    """For every case whose SQL the mutation changes: passed == naive identity. Returns how many it changed."""
    changed = 0
    records = {r.case_id: r for r in await run_cases(gold.cases, MutantAnswerer(gold, mutation), db)}
    for case in gold.cases:
        if case.gold_sql is None or not changes(mutation, case.gold_sql):
            continue

        changed += 1
        gold_result = await db.execute(case.gold_sql)
        try:
            same = identical(gold_result, await db.execute(mutation(case.gold_sql)), ordered=case.ordered)
        except EvalSqlError:
            same = False
        assert records[case.id].passed == same, f"{case.id}: {records[case.id].diff}"
    return changed


async def failed_ids(gold: GoldSet, answerer: Answerer, db: EvalDatabase) -> list[str]:
    return [r.case_id for r in await run_cases(gold.cases, answerer, db) if not r.passed]


async def assert_every_mutation_bites(gold: GoldSet, mutation: Callable[[str], str], db: EvalDatabase) -> None:
    assert await failed_ids(gold, MutantAnswerer(gold, mutation), db)


async def assert_always_answered_fails_all(gold: GoldSet, db: EvalDatabase) -> None:
    assert sorted(await failed_ids(gold, AlwaysAnsweredAnswerer(), db)) == sorted(c.id for c in gold.cases)


class TestFixtureCases:
    @pytest.mark.parametrize("name", MUTATIONS)
    async def test_a_mutant_passes_only_when_its_result_is_identical(self, name: str, db: EvalDatabase) -> None:
        changed = await assert_pass_equals_identity(FIXTURE_GOLD, MUTATIONS[name], db)

        assert changed >= 1

    @pytest.mark.parametrize("name", MUTATIONS)
    async def test_each_mutation_fails_at_least_one_case(self, name: str, db: EvalDatabase) -> None:
        await assert_every_mutation_bites(FIXTURE_GOLD, MUTATIONS[name], db)

    async def test_dropping_the_cancelled_filter_leaves_net_revenue_alone(self, db: EvalDatabase) -> None:
        records = await run_cases(
            FIXTURE_GOLD.cases[:1], MutantAnswerer(FIXTURE_GOLD, MUTATIONS["drop_cancelled_filter"]), db
        )

        assert records[0].passed

    async def test_always_answered_fails_every_case(self, db: EvalDatabase) -> None:
        await assert_always_answered_fails_all(FIXTURE_GOLD, db)


class TestAcceptedGoldSet:
    @pytest.mark.parametrize("name", MUTATIONS)
    async def test_a_mutant_passes_only_when_its_result_is_identical(
        self, name: str, accepted_gold: GoldSet, db: EvalDatabase
    ) -> None:
        await assert_pass_equals_identity(accepted_gold, MUTATIONS[name], db)

    @pytest.mark.parametrize("name", MUTATIONS)
    async def test_each_mutation_fails_at_least_one_case(
        self, name: str, accepted_gold: GoldSet, db: EvalDatabase
    ) -> None:
        await assert_every_mutation_bites(accepted_gold, MUTATIONS[name], db)

    async def test_always_answered_fails_every_case(self, accepted_gold: GoldSet, db: EvalDatabase) -> None:
        await assert_always_answered_fails_all(accepted_gold, db)
