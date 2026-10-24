from __future__ import annotations

from pathlib import Path

import pytest
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.examples import Example, ExampleIndex
from sherpa_mcp.sql_guard import SqlGuard

EXAMPLES_YAML = Path(__file__).parents[1] / "src" / "sherpa_mcp" / "examples.yaml"
PROD_TARGET = Path(__file__).parents[3] / "data-platform" / "dbt" / "target-prod"
# Data platform spec §6.5.
MARTS = frozenset(
    {
        *("dim_customers", "dim_products", "dim_promotions", "dim_date"),
        *("fct_orders", "fct_order_items", "fct_order_discounts", "fct_returns", "fct_refunds"),
    }
)


def _example(question: str) -> Example:
    return Example(question=question, sql="select 1", notes="")


class TestExampleIndex:
    def test_top_ranks_by_jaccard(self) -> None:
        index = ExampleIndex(
            [
                _example("monthly revenue by country"),
                _example("customers by segment"),
                _example("revenue by month"),
            ]
        )

        top = index.top("revenue by month", 2)

        assert [e.question for e in top] == ["revenue by month", "monthly revenue by country"]

    def test_ties_keep_file_order(self) -> None:
        index = ExampleIndex([_example("alpha one"), _example("alpha two"), _example("alpha three")])

        top = index.top("alpha", 3)

        assert [e.question for e in top] == ["alpha one", "alpha two", "alpha three"]

    @pytest.mark.parametrize("k", [0, 11])
    def test_k_bounds(self, k: int) -> None:
        index = ExampleIndex([_example("alpha")])

        with pytest.raises(ValueError, match="k must be between 1 and 10"):
            index.top("alpha", k)

    def test_question_with_only_stop_words_returns_first_k(self) -> None:
        index = ExampleIndex([_example("alpha"), _example("beta"), _example("gamma")])

        top = index.top("what is the", 2)

        assert [e.question for e in top] == ["alpha", "beta"]

    def test_fewer_examples_than_k(self) -> None:
        index = ExampleIndex([_example("alpha")])

        assert len(index.top("alpha", 5)) == 1


def test_examples_yaml_passes_the_guard() -> None:
    marts = MARTS
    if PROD_TARGET.joinpath("catalog.json").exists():
        marts = MartCatalogLoader(PROD_TARGET).load().visible_names()
        assert marts == MARTS
    index = ExampleIndex.from_yaml(EXAMPLES_YAML)
    guard = SqlGuard(marts)

    assert len(index.examples) == 15
    for example in index.examples:
        result = guard.check(example.sql)
        assert result.ok, f"{example.question}: {result.errors}"
        assert example.notes
