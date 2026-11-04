"""The real gold set: its versions, tranche-1 shape and few-shot disjointness (agent spec §4.3, §7.1)."""

from __future__ import annotations

import re
from collections import Counter

import pytest
from sherpa_evals.cases import GoldSet, static_problems
from sherpa_evals.settings import CASES_PATH
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.server import EXAMPLES_PATH

# The data-platform-2 marts, the only ones tranche 1 may read (agent spec §7.1).
TRANCHE_1_MARTS = frozenset(
    {
        "dim_customers",
        "dim_products",
        "dim_promotions",
        "dim_date",
        "fct_orders",
        "fct_order_items",
        "fct_order_discounts",
        "fct_returns",
        "fct_refunds",
    }
)
TRANCHE_1_COUNTS = {
    "values": 8,
    "metrics": 8,
    "grain": 7,
    "time": 6,
    "complex": 5,
    "unanswerable": 3,
    "clarification": 3,
}


@pytest.fixture(scope="module")
def gold() -> GoldSet:
    return GoldSet.load(CASES_PATH)


def test_versions_are_one(gold: GoldSet) -> None:
    assert (gold.gold_version, gold.dataset_version) == (1, 1)


def test_tranche_1_has_the_spec_category_counts(gold: GoldSet) -> None:
    assert {case.tranche for case in gold.cases} == {1}
    assert Counter(case.category for case in gold.cases) == TRANCHE_1_COUNTS


def test_answered_cases_read_only_tranche_1_marts(gold: GoldSet) -> None:
    problems = {case.id: static_problems(case, TRANCHE_1_MARTS) for case in gold.cases}

    assert {case_id: found for case_id, found in problems.items() if found} == {}


def test_no_question_repeats_a_few_shot_question(gold: GoldSet) -> None:
    examples = {_words(example.question) for example in ExampleIndex.from_yaml(EXAMPLES_PATH).examples}

    assert [case.id for case in gold.cases if _words(case.question) in examples] == []


def _words(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.lower()))
