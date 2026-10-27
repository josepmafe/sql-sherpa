from __future__ import annotations

from typing import Any

from sherpa_ui.lineage.manifest_graph import Layer, ManifestGraph


class TestManifestGraph:
    def test_layers_from_the_model_folder(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert graph.nodes["model.p.stg_shop__orders"].layer is Layer.STAGING
        assert graph.nodes["model.p.int_orders__enriched"].layer is Layer.INTERMEDIATE
        assert graph.nodes["model.p.fct_orders"].layer is Layer.MARTS
        assert graph.nodes["source.p.shop.orders"].layer is Layer.SOURCE
        assert graph.nodes["seed.p.countries"].layer is Layer.SEED
        assert graph.nodes["snapshot.p.snap_customers"].layer is Layer.SNAPSHOT
        assert graph.nodes["exposure.p.sales_dashboard"].layer is Layer.EXPOSURE
        assert graph.layers() == (
            Layer.SOURCE,
            Layer.SEED,
            Layer.STAGING,
            Layer.SNAPSHOT,
            Layer.INTERMEDIATE,
            Layer.MARTS,
            Layer.EXPOSURE,
        )

    def test_a_source_node_keeps_its_source_name(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert graph.nodes["source.p.shop.orders"].source_name == "shop"
        assert graph.nodes["model.p.fct_orders"].source_name is None

    def test_a_model_outside_the_known_folders_is_other(self, manifest: dict[str, Any]) -> None:
        manifest["nodes"]["model.p.fct_orders"]["fqn"] = ["p", "adhoc", "fct_orders"]

        graph = ManifestGraph.from_manifest(manifest)

        assert graph.nodes["model.p.fct_orders"].layer is Layer.OTHER
        assert Layer.OTHER in graph.layers()

    def test_node_fields_come_from_the_manifest(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        fct = graph.nodes["model.p.fct_orders"]
        assert (fct.name, fct.materialization, fct.domain, fct.contract_enforced) == (
            "fct_orders",
            "table",
            "sales",
            True,
        )
        assert fct.raw_sql == "select 1 as fct_orders"
        assert [(column.name, column.data_type) for column in fct.columns] == [("id", "bigint")]
        assert graph.nodes["seed.p.countries"].raw_sql is None
        assert graph.nodes["source.p.shop.orders"].materialization is None
        assert graph.nodes["source.p.shop.orders"].columns[0].data_type is None

    def test_tests_and_hooks_are_not_nodes(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert {node.resource_type for node in graph.nodes.values()} == {
            "source",
            "seed",
            "model",
            "snapshot",
            "exposure",
        }
        assert len(graph.nodes) == 3 + 1 + 1 + 6 + 1

    def test_generic_test_counts_against_its_attached_node(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        tests = [test for test in graph.tests_of("model.p.fct_orders") if test.kind == "generic"]

        assert [(test.name, test.column, test.severity) for test in tests] == [
            ("not_null_fct_orders_order_id", "order_id", "error")
        ]
        assert tests[0].counts_against == ("model.p.fct_orders",)

    def test_singular_test_counts_against_every_node_it_depends_on(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        for unique_id in ("model.p.fct_orders", "model.p.dim_customers"):
            names = [test.name for test in graph.tests_of(unique_id) if test.kind == "singular"]
            assert names == ["assert_orders_match_customers"]
        assert graph.tests_of("model.p.dim_date") == ()

    def test_unit_test_counts_against_the_model_it_checks(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert [test.name for test in graph.tests_of("model.p.fct_orders") if test.kind == "unit"] == [
            "revenue_is_summed"
        ]
        assert [test.kind for test in graph.tests_of("model.p.int_orders__enriched")] == []

    def test_macros_are_not_parents(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert graph.parents("model.p.int_orders__enriched") == ("model.p.stg_shop__orders", "seed.p.countries")

    def test_children_are_the_inverse_of_parents(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert set(graph.children("model.p.fct_orders")) == {"model.p.dim_customers", "exposure.p.sales_dashboard"}
        assert graph.children("exposure.p.sales_dashboard") == ()

    def test_severity_is_normalised_to_lower_case(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        generic = next(test for test in graph.tests_of("model.p.dim_customers") if test.kind == "generic")
        assert generic.severity == "warn"

    def test_file_path_uses_forward_slashes(self, manifest: dict[str, Any]) -> None:
        manifest["nodes"]["model.p.fct_orders"]["original_file_path"] = "models\\marts\\x.sql"

        graph = ManifestGraph.from_manifest(manifest)

        assert graph.nodes["model.p.fct_orders"].file_path == "models/marts/x.sql"

    def test_generated_at_is_parsed(self, manifest: dict[str, Any]) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert graph.generated_at.isoformat() == "2026-10-27T10:58:39.861036+00:00"
