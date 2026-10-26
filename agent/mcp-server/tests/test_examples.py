from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.examples import Example, ExampleIndex
from sherpa_mcp.sql_guard import SqlGuard

EXAMPLES_YAML = Path(__file__).parents[1] / "src" / "sherpa_mcp" / "examples.yaml"
PROD_TARGET = Path(__file__).parents[3] / "data-platform" / "dbt" / "target-prod"
MARTS_MODELS = Path(__file__).parents[3] / "data-platform" / "dbt" / "models" / "marts"
# Data platform spec §6.5.
MARTS = frozenset(
    {
        *("dim_customers", "dim_products", "dim_promotions", "dim_date"),
        *("fct_orders", "fct_order_items", "fct_order_discounts", "fct_returns", "fct_refunds"),
        "fct_sessions",
    }
)


def _mart_columns() -> dict[str, frozenset[str]]:
    """The built catalog's marts and columns; without a prod build, the same names from the marts' dbt YAML."""
    if PROD_TARGET.joinpath("catalog.json").exists():
        return MartCatalogLoader(PROD_TARGET).load().mart_columns()
    models = [
        model
        for path in MARTS_MODELS.glob("*/_*__models.yml")
        for model in yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    ]
    return {model["name"]: frozenset(column["name"] for column in model.get("columns", [])) for model in models}


def _example(question: str, notes: str = "", sql: str = "select 1") -> Example:
    return Example(question=question, sql=sql, notes=notes)


class TestExampleIndex:
    def test_top_ranks_by_weighted_jaccard_with_scores(self) -> None:
        index = ExampleIndex(
            [
                _example("monthly revenue by country"),
                _example("customers by segment"),
                _example("revenue by month"),
            ]
        )

        top = index.top("revenue by month", 3)

        # {revenue, month} against {month, revenue, country}: 2/3 on the question and on everything.
        assert [(e.question, e.score) for e in top] == [
            ("revenue by month", 1.0),
            ("monthly revenue by country", 0.667),
        ]

    def test_ties_keep_file_order(self) -> None:
        index = ExampleIndex([_example("alpha one"), _example("alpha two"), _example("alpha three")])

        top = index.top("alpha", 3)

        assert [e.question for e in top] == ["alpha one", "alpha two", "alpha three"]

    @pytest.mark.parametrize("k", [0, 11])
    def test_k_bounds(self, k: int) -> None:
        index = ExampleIndex([_example("alpha")])

        with pytest.raises(ValueError, match="k must be between 1 and 10"):
            index.top("alpha", k)

    @pytest.mark.parametrize("question", ["what is the", "", "weather forecast"])
    def test_question_sharing_no_word_returns_nothing(self, question: str) -> None:
        index = ExampleIndex([_example("alpha"), _example("beta"), _example("gamma")])

        assert index.top(question, 2) == []

    def test_examples_sharing_no_word_are_left_out(self) -> None:
        index = ExampleIndex([_example("alpha"), _example("beta"), _example("alpha beta")])

        assert [e.question for e in index.top("alpha", 3)] == ["alpha", "alpha beta"]

    def test_fewer_examples_than_k(self) -> None:
        index = ExampleIndex([_example("alpha")])

        assert len(index.top("alpha", 5)) == 1

    @pytest.mark.parametrize(
        ("question", "example"),
        [
            ("monthly orders", "orders per month"),
            ("cancelled orders", "cancel an order"),
            ("refunded categories", "refund per category"),
            ("boxes shipped", "box shipping"),
            ("purchases", "purchase"),
            ("increases", "increase"),
            ("purchased", "purchasing"),
            ("placed", "place"),
            ("received", "receive"),
            ("sizes", "size"),
            ("classes", "class"),
            ("statuses", "status"),
            ("pressed", "press"),
        ],
    )
    def test_word_forms_meet_on_their_stem(self, question: str, example: str) -> None:
        index = ExampleIndex([_example(example)])

        assert index.top(question, 1)[0].score == 1.0

    @pytest.mark.parametrize(("question", "example"), [("sales", "sal"), ("status", "statu")])
    def test_stemming_keeps_words_apart_that_differ(self, question: str, example: str) -> None:
        # "sales" stems to "sale", never to "sal"; "status" keeps its s. Neither shares a stem with the example.
        index = ExampleIndex([_example(example)])

        assert index.top(question, 1) == []

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("AOV", "average order value"),
            ("coupon use", "discount by promotion"),
            ("yoy", "revenue per year"),
            ("units sold", "item quantity"),
        ],
    )
    def test_synonyms_reach_the_examples_wording(self, question: str, expected: str) -> None:
        index = ExampleIndex([_example("weather"), _example(expected)])

        assert [e.question for e in index.top(question, 2)] == [expected]

    def test_notes_and_sql_names_count_at_a_lower_weight(self) -> None:
        by_question = _example("refund reasons")
        by_notes = _example("why items come back", notes="grouped by refund reason")
        by_sql = _example("why items come back", sql="select reason from marts.fct_refunds")
        index = ExampleIndex([by_sql, by_notes, by_question])

        top = index.top("refund reason", 3)

        # A matching question: 0.75 x 1 + 0.25 x 1. Matching notes or SQL names only: 0.25 x 2/7, the two words
        # among why, item, come, back plus group, refund, reason (notes) or fct, refund, reason (SQL).
        assert [(e.notes, e.sql, e.score) for e in top] == [
            ("", "select 1", 1.0),
            ("", "select reason from marts.fct_refunds", 0.071),
            ("grouped by refund reason", "select 1", 0.071),
        ]

    def test_examples_yaml_ranks_each_question_first(self) -> None:
        index = ExampleIndex.from_yaml(EXAMPLES_YAML)

        for example in index.examples:
            assert index.top(example.question, 1)[0].question == example.question


def test_examples_yaml_passes_the_guard() -> None:
    mart_columns = _mart_columns()
    assert set(mart_columns) == MARTS
    index = ExampleIndex.from_yaml(EXAMPLES_YAML)
    guard = SqlGuard(mart_columns)

    assert len(index.examples) == 19
    for example in index.examples:
        result = guard.check(example.sql)
        assert result.ok, f"{example.question}: {result.errors}"
        assert example.notes
