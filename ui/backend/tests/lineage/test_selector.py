from __future__ import annotations

from typing import Any

import pytest
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.selector import SelectorEvaluator, UnsupportedSelector

ORDERS_UPSTREAM = {
    "source.p.shop.orders",
    "model.p.stg_shop__orders",
    "seed.p.countries",
    "model.p.int_orders__enriched",
}
CUSTOMERS_UPSTREAM = {"source.p.shop.customers", "model.p.stg_shop__customers", "snapshot.p.snap_customers"}
FCT = "model.p.fct_orders"
DIM_CUSTOMERS = "model.p.dim_customers"
DASHBOARD = "exposure.p.sales_dashboard"


@pytest.fixture
def evaluator(manifest: dict[str, Any]) -> SelectorEvaluator:
    return SelectorEvaluator(ManifestGraph.from_manifest(manifest))


class TestSelectorEvaluator:
    def test_a_name_selects_only_that_node(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("fct_orders") == {FCT}
        assert evaluator.select("countries") == {"seed.p.countries"}
        assert evaluator.select("snap_customers") == {"snapshot.p.snap_customers"}
        assert evaluator.select("sales_dashboard") == {DASHBOARD}

    def test_a_bare_name_does_not_select_a_source_table(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("leads") == frozenset()  # as in dbt: a source table is source:<source>.<table>
        assert evaluator.select("source:crm.leads") == {"source.p.crm.leads"}

    def test_upstream_operator_adds_all_ancestors(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("+fct_orders") == ORDERS_UPSTREAM | {FCT}

    def test_downstream_operator_adds_all_descendants(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("fct_orders+") == {FCT, DIM_CUSTOMERS, DASHBOARD}

    def test_a_depth_limits_the_upstream_walk(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("1+fct_orders") == {FCT, "model.p.int_orders__enriched"}
        assert evaluator.select("2+fct_orders") == {
            FCT,
            "model.p.int_orders__enriched",
            "model.p.stg_shop__orders",
            "seed.p.countries",
        }

    def test_a_depth_limits_the_downstream_walk(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("stg_shop__customers+1") == {"model.p.stg_shop__customers", "snapshot.p.snap_customers"}

    def test_both_depths_apply_together(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("1+int_orders__enriched+1") == {
            "model.p.int_orders__enriched",
            "model.p.stg_shop__orders",
            "seed.p.countries",
            FCT,
        }

    def test_a_depth_on_one_side_leaves_the_other_unlimited(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("1+fct_orders+") == {FCT, "model.p.int_orders__enriched", DIM_CUSTOMERS, DASHBOARD}
        assert evaluator.select("+fct_orders+1") == ORDERS_UPSTREAM | {FCT, DIM_CUSTOMERS, DASHBOARD}

    def test_source_selects_every_table_of_the_source(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("source:shop") == {"source.p.shop.orders", "source.p.shop.customers"}

    def test_source_table_selects_that_table(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("source:shop.orders") == {"source.p.shop.orders"}
        assert evaluator.select("source:shop.orders+1") == {"source.p.shop.orders", "model.p.stg_shop__orders"}

    def test_exposure_selects_the_exposure(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("exposure:sales_dashboard") == {DASHBOARD}
        assert evaluator.select("+exposure:sales_dashboard") == ORDERS_UPSTREAM | {FCT, DASHBOARD}

    def test_a_model_name_does_not_match_an_exposure_prefix(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("exposure:fct_orders") == frozenset()

    def test_domain_selects_the_marts_of_the_domain(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("config.meta.domain:sales") == {FCT, DIM_CUSTOMERS}
        assert evaluator.select("config.meta.domain:shared") == {"model.p.dim_date"}

    def test_domain_with_upstream_adds_every_ancestor(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("+config.meta.domain:sales") == (
            ORDERS_UPSTREAM | CUSTOMERS_UPSTREAM | {FCT, DIM_CUSTOMERS}
        )

    def test_unions_merge_results(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("dim_date  fct_orders+1") == {"model.p.dim_date", FCT, DIM_CUSTOMERS, DASHBOARD}

    @pytest.mark.parametrize("selector", ["", "   ", "\n\t"])
    def test_blank_selects_everything(self, manifest: dict[str, Any], selector: str) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert SelectorEvaluator(graph).select(selector) == set(graph.nodes)

    @pytest.mark.parametrize("selector", ["no_such_model", "+no_such_model+", "source:nope", "source:shop.nope"])
    def test_unknown_name_selects_nothing(self, evaluator: SelectorEvaluator, selector: str) -> None:
        assert evaluator.select(selector) == frozenset()

    @pytest.mark.parametrize(
        "selector",
        [
            "+",
            "++fct_orders",
            "fct_orders++",
            "fct_orders,dim_customers",
            "@fct_orders",
            "tag:x",
            "path:models/marts",
            "fct_orders+dim_customers",
            "*",
            "fct_orders*",
            "-1+fct_orders",
            "config.materialized:table",
            "source:",
            "source:a.b.c",
            "exposure:",
            "x" * 1001,
            "marts",
            "staging",
            "intermediate",
            "p",
            "singular",
            "+marts",
            "marts+1",
            "assert_orders_match_customers",
            "+assert_orders_match_customers",
            "revenue_is_summed",
            "scratch",
        ],
    )
    def test_unsupported_forms_name_what_is_supported(self, evaluator: SelectorEvaluator, selector: str) -> None:
        with pytest.raises(UnsupportedSelector) as error:
            evaluator.select(selector)

        assert "source:<source>.<table>" in error.value.message
        assert "config.meta.domain:<domain>" in error.value.message
        assert "n+atom+m" in error.value.message

    def test_a_folder_name_points_to_the_layer_filter(self, evaluator: SelectorEvaluator) -> None:
        with pytest.raises(UnsupportedSelector) as error:
            evaluator.select("marts")

        assert "layer filter" in error.value.message

    def test_a_folder_name_that_is_also_a_node_name_is_still_rejected(self, manifest: dict[str, Any]) -> None:
        manifest["nodes"]["model.p.fct_orders"]["name"] = "intermediate"

        with pytest.raises(UnsupportedSelector):
            SelectorEvaluator(ManifestGraph.from_manifest(manifest)).select("intermediate")

    def test_a_test_name_that_is_also_a_node_name_selects_the_node(self, manifest: dict[str, Any]) -> None:
        manifest["nodes"]["model.p.fct_orders"]["name"] = "scratch"

        assert SelectorEvaluator(ManifestGraph.from_manifest(manifest)).select("scratch") == {FCT}

    def test_only_the_package_and_first_folder_are_reserved_not_deeper_folders(self, manifest: dict[str, Any]) -> None:
        manifest["nodes"]["model.p.fct_orders"]["fqn"] = ["p", "marts", "sales", "fct_orders"]
        evaluator = SelectorEvaluator(ManifestGraph.from_manifest(manifest))

        assert evaluator.select("sales") == frozenset()  # dbt selects nothing for a sub-folder name
        with pytest.raises(UnsupportedSelector):
            evaluator.select("marts")
        with pytest.raises(UnsupportedSelector):
            evaluator.select("p")

    def test_a_selector_of_exactly_the_limit_is_evaluated(self, evaluator: SelectorEvaluator) -> None:
        assert evaluator.select("x" * 1000) == frozenset()
