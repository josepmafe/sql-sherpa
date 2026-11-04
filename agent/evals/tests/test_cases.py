from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError
from sherpa_evals.cases import GoldSet, static_problems

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sherpa_evals.cases import GoldCase

MARTS = frozenset(
    {
        "fct_orders",
        "fct_order_items",
        "dim_customers",
        "dim_products",
        "dim_date",
        "fct_sessions",
        "fct_events",
        "dim_campaigns",
        "fct_campaign_spend",
    }
)
CLOCK = "reads the clock"
HASH = "holds a 32-hex literal; hashes change with every eval-db rebuild"
ORDER = "ordered or limited without ORDER BY"
HASH_LITERAL = "d41d8cd98f00b204e9800998ecf8427e"


class TestGoldCase:
    def test_answered_case_without_gold_sql_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="gold_sql"):
            make_case(gold_sql=None)

    def test_answered_case_without_tables_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="tables"):
            make_case(tables=())

    def test_unanswerable_case_with_gold_sql_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="gold_sql"):
            make_case("unanswerable-001", expected_outcome="unanswerable", tables=())

    def test_unanswerable_case_with_reconcile_sql_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="reconcile_sql"):
            make_case(
                "unanswerable-001", expected_outcome="unanswerable", gold_sql=None, tables=(), reconcile_sql="select 1"
            )

    def test_unanswerable_case_with_tables_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="tables"):
            make_case("unanswerable-001", expected_outcome="unanswerable", gold_sql=None)

    def test_unanswerable_case_without_sql_is_valid(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("unanswerable-001", expected_outcome="unanswerable", gold_sql=None, tables=())

        assert case.gold_sql is None

    def test_id_with_another_category_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="id"):
            make_case("metrics-003", category="grain")

    def test_id_without_three_digits_is_refused(self, make_case: Callable[..., GoldCase]) -> None:
        with pytest.raises(ValidationError, match="id"):
            make_case("metrics-3", category="metrics")


class TestGoldSet:
    def test_duplicate_ids_are_refused(self, make_case: Callable[..., GoldCase]) -> None:
        cases = (make_case("values-001"), make_case("values-001", question="Another question?"))

        with pytest.raises(ValidationError, match="duplicate id"):
            GoldSet(gold_version=1, dataset_version=1, cases=cases)

    def test_duplicate_questions_are_refused(self, make_case: Callable[..., GoldCase]) -> None:
        cases = (make_case("values-001", question="Same?"), make_case("values-002", question="Same?"))

        with pytest.raises(ValidationError, match="duplicate question"):
            GoldSet(gold_version=1, dataset_version=1, cases=cases)

    def test_accepted_keeps_only_accepted_cases(self, make_case: Callable[..., GoldCase]) -> None:
        cases = (
            make_case("values-001", status="accepted"),
            make_case("values-002", status="reviewed"),
            make_case("values-003", status="draft"),
        )

        gold = GoldSet(gold_version=1, dataset_version=1, cases=cases)

        assert [case.id for case in gold.accepted()] == ["values-001"]

    def test_loads_a_two_case_yaml(self, tmp_path: Path) -> None:
        document = {
            "gold_version": 3,
            "dataset_version": 2,
            "cases": [
                {
                    "id": "values-001",
                    "category": "values",
                    "question": "How many orders?",
                    "tranche": 1,
                    "expected_outcome": "answered",
                    "gold_sql": "select count(*) from marts.fct_orders",
                    "tables": ["fct_orders"],
                    "rationale": "Counts orders.",
                    "status": "accepted",
                    "reviews": [{"reviewer": "codex", "verdict": "accept", "notes": "ok"}],
                },
                {
                    "id": "unanswerable-001",
                    "category": "unanswerable",
                    "question": "What is the weather?",
                    "tranche": 1,
                    "expected_outcome": "unanswerable",
                    "gold_sql": None,
                    "rationale": "No weather data.",
                },
            ],
        }
        path = tmp_path / "cases.yaml"
        path.write_text(yaml.safe_dump(document))

        gold = GoldSet.load(path)

        assert (gold.gold_version, gold.dataset_version) == (3, 2)
        assert [case.id for case in gold.cases] == ["values-001", "unanswerable-001"]
        assert gold.cases[0].tables == ("fct_orders",)
        assert gold.cases[0].reviews[0].reviewer == "codex"


class TestStaticChecks:
    def test_clean_query_has_no_problem(self, make_case: Callable[..., GoldCase]) -> None:
        assert static_problems(make_case(), MARTS) == []

    @pytest.mark.parametrize(
        "sql",
        [
            "select count(*) from marts.fct_orders where order_date = current_date",
            "select count(*) from marts.fct_orders where created_at < now()",
            "select count(*) from marts.fct_orders where created_at < current_timestamp",
        ],
    )
    def test_clock_reads_are_flagged(self, make_case: Callable[..., GoldCase], sql: str) -> None:
        problems = static_problems(make_case(gold_sql=sql), MARTS)

        assert len(problems) == 1
        assert problems[0].startswith(CLOCK)
        assert "(select max(order_date) from marts.fct_orders)" in problems[0]

    def test_hash_literal_in_gold_sql_is_flagged(self, make_case: Callable[..., GoldCase]) -> None:
        sql = f"select count(*) from marts.fct_orders where email = '{HASH_LITERAL}'"

        assert static_problems(make_case(gold_sql=sql), MARTS) == [HASH]

    def test_hash_literal_in_reconcile_sql_only_is_flagged(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case(reconcile_sql=f"select 1 where 'x' = '{HASH_LITERAL.upper()}'")

        assert static_problems(case, MARTS) == [HASH]

    def test_long_hex_outside_a_string_is_not_a_hash(self, make_case: Callable[..., GoldCase]) -> None:
        sql = "select count(*) as d41d8cd98f00b204e9800998ecf8427e from marts.fct_orders"

        assert static_problems(make_case(gold_sql=sql), MARTS) == []

    def test_limit_without_order_by_is_flagged(self, make_case: Callable[..., GoldCase]) -> None:
        sql = "select order_id from marts.fct_orders limit 10"

        assert static_problems(make_case(gold_sql=sql), MARTS) == [ORDER]

    def test_ordered_without_order_by_is_flagged(self, make_case: Callable[..., GoldCase]) -> None:
        assert static_problems(make_case(ordered=True), MARTS) == [ORDER]

    def test_limit_with_order_by_is_clean(self, make_case: Callable[..., GoldCase]) -> None:
        sql = "select order_id from marts.fct_orders order by order_id limit 10"

        assert static_problems(make_case(gold_sql=sql, ordered=True), MARTS) == []

    def test_order_by_only_in_a_subquery_does_not_count(self, make_case: Callable[..., GoldCase]) -> None:
        sql = "select order_id from (select order_id from marts.fct_orders order by order_id) o limit 10"

        assert static_problems(make_case(gold_sql=sql), MARTS) == [ORDER]

    def test_tables_must_match_the_marts_the_sql_reads(self, make_case: Callable[..., GoldCase]) -> None:
        sql = "select count(*) from marts.fct_orders o join marts.dim_customers c on c.customer_id = o.customer_id"

        problems = static_problems(make_case(gold_sql=sql, tables=("fct_orders",)), MARTS)

        assert problems == ["tables lists {'fct_orders'}, the SQL reads {'dim_customers', 'fct_orders'}"]

    def test_unqualified_table_is_the_guards_message(self, make_case: Callable[..., GoldCase]) -> None:
        problems = static_problems(make_case(gold_sql="select count(*) from fct_orders"), MARTS)

        assert len(problems) == 1
        assert problems[0].startswith("[guard:table] table fct_orders is not schema-qualified")
