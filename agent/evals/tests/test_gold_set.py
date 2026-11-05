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
MIN_REVIEWERS = 2  # Codex and Gemini, or a fresh Opus session in place of one (agent spec §7.1 step 3)
MIN_DEFAULT_REVENUE_CASES = 3  # agent spec §7.1 metrics
MIN_RECONCILED_CASES = 3  # agent-design-2 plan, Task 5
QUALIFIED_REVENUE = re.compile(r"\b(net|gross|refunded)\s+revenue\b")
# Wording that asks for the rows in an order: "longest first", "ranked", "sorted", "from highest to lowest".
ORDER_WORDS = re.compile(
    r"\b(\w+est first|rank(ed)?|sort(ed)?|in order|ascending|descending|from \w+est to \w+est)\b", re.I
)


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


def test_reviews_follow_the_status(gold: GoldSet) -> None:
    drafts_with_reviews = [case.id for case in gold.cases if case.status == "draft" and case.reviews]
    accepted_without_two_reviewers = [
        case.id for case in gold.accepted() if len({review.reviewer for review in case.reviews}) < MIN_REVIEWERS
    ]

    assert drafts_with_reviews == []
    assert accepted_without_two_reviewers == []


def test_at_least_three_metrics_cases_take_unqualified_revenue_as_net_revenue(gold: GoldSet) -> None:
    defaulted = [
        case.id
        for case in gold.cases
        if case.category == "metrics"
        and case.expected_outcome == "answered"
        and _has_unqualified_revenue(case.question)
        and re.search(r"\bnet_revenue\b", case.gold_sql or "")
    ]

    assert len(defaulted) >= MIN_DEFAULT_REVENUE_CASES, defaulted


def test_at_least_three_answered_cases_reconcile_with_the_raw_schemas(gold: GoldSet) -> None:
    reconciled = [case.id for case in gold.cases if case.expected_outcome == "answered" and case.reconcile_sql]

    assert len(reconciled) >= MIN_RECONCILED_CASES, reconciled


def test_only_questions_that_ask_for_an_order_are_ordered(gold: GoldSet) -> None:
    unasked = [case.id for case in gold.cases if case.ordered and not ORDER_WORDS.search(case.question)]

    assert unasked == []


def _words(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.lower()))


def _has_unqualified_revenue(question: str) -> bool:
    lowered = question.lower()
    return len(re.findall(r"\brevenue\b", lowered)) > len(QUALIFIED_REVENUE.findall(lowered))
