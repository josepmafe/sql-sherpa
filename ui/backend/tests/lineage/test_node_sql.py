from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
