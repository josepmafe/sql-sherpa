from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.node_sql import NodeSql

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.lineage.manifest_graph import GraphNode


def _write(target: Path, relative: str, text: str) -> None:
    path = target / "compiled" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestNodeSql:
    def test_reads_a_model_file(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/models/marts/fct_orders.sql", "select compiled")

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) == "select compiled"

    def test_reads_the_file_of_a_manifest_written_on_windows(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        manifest["nodes"]["model.p.fct_orders"]["original_file_path"] = "models\\marts\\fct_orders.sql"
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/models/marts/fct_orders.sql", "select compiled")

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) == "select compiled"

    def test_reads_a_yaml_defined_node(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/snapshots/_snapshots.yml/snap_customers.sql", "select snapshot")

        assert NodeSql(tmp_path).compiled(graph.nodes["snapshot.p.snap_customers"]) == "select snapshot"

    def test_a_path_outside_compiled_is_not_read(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        manifest["nodes"]["model.p.fct_orders"]["original_file_path"] = "../../secret.sql"
        graph = ManifestGraph.from_manifest(manifest)
        (tmp_path / "compiled").mkdir()
        (tmp_path / "secret.sql").write_text("select secret", encoding="utf-8")

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) is None

    def test_a_file_that_is_not_utf8_is_none(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        path = tmp_path / "compiled" / "p" / "models" / "marts" / "fct_orders.sql"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"select \xff\xfe")

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) is None

    def test_a_path_with_a_nul_byte_is_none(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        manifest["nodes"]["model.p.fct_orders"]["original_file_path"] = "models/marts/fct\x00_orders.sql"
        graph = ManifestGraph.from_manifest(manifest)

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) is None

    def test_missing_file_is_none(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        assert NodeSql(tmp_path).compiled(graph.nodes["model.p.fct_orders"]) is None

    def test_a_directory_in_place_of_the_file_is_none(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        node: GraphNode = graph.nodes["model.p.fct_orders"]
        (tmp_path / "compiled" / "p" / node.file_path).mkdir(parents=True)

        assert NodeSql(tmp_path).compiled(node) is None


class TestNodeSqlTests:
    def test_a_singular_test_reads_its_file_under_its_package(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/tests/singular/assert_orders_match_customers.sql", "select singular")

        test = graph.test("test.p.assert_orders_match_customers")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) == "select singular"

    def test_a_generic_test_resolves_by_the_manifest_path_not_its_name(
        self, manifest: dict[str, Any], tmp_path: Path
    ) -> None:
        manifest["nodes"]["test.p.not_null_fct_orders_order_id"]["path"] = "not_null_fct_orders_e1f2a3b4c5.sql"
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/models/marts/_models.yml/not_null_fct_orders_e1f2a3b4c5.sql", "select shortened")
        _write(tmp_path, "p/models/marts/_models.yml/not_null_fct_orders_order_id.sql", "select by name")

        test = graph.test("test.p.not_null_fct_orders_order_id")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) == "select shortened"

    @pytest.mark.parametrize(
        ("crafted", "secret"),
        [
            ("../../../../../x.sql", "x.sql"),  # out of the target path
            ("../../../../x.sql", "compiled/x.sql"),  # out of the package's folder, still under compiled/
        ],
    )
    def test_a_crafted_path_does_not_leave_the_package_folder(
        self, manifest: dict[str, Any], tmp_path: Path, crafted: str, secret: str
    ) -> None:
        manifest["nodes"]["test.p.not_null_fct_orders_order_id"]["path"] = crafted
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/models/marts/_models.yml/.keep", "")
        (tmp_path / secret).write_text("select secret", encoding="utf-8")

        test = graph.test("test.p.not_null_fct_orders_order_id")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) is None

    def test_a_crafted_file_path_does_not_leave_compiled(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        manifest["nodes"]["test.p.assert_orders_match_customers"]["original_file_path"] = "../../x"
        graph = ManifestGraph.from_manifest(manifest)
        (tmp_path / "compiled").mkdir()
        (tmp_path / "x").write_text("select secret", encoding="utf-8")

        test = graph.test("test.p.assert_orders_match_customers")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) is None

    def test_a_missing_file_is_none(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)

        test = graph.test("test.p.not_null_fct_orders_order_id")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) is None

    def test_a_unit_test_has_no_compiled_file(self, manifest: dict[str, Any], tmp_path: Path) -> None:
        graph = ManifestGraph.from_manifest(manifest)
        _write(tmp_path, "p/models/marts/_models.yml/revenue_is_summed.sql", "select not a unit test file")

        test = graph.test("unit_test.p.fct_orders.revenue_is_summed")

        assert test is not None
        assert NodeSql(tmp_path).compiled_test(test) is None
