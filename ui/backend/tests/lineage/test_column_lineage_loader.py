from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_ui.lineage import column_lineage_loader
from sherpa_ui.lineage.column_lineage import ColumnLineage, ColumnRef
from sherpa_ui.lineage.column_lineage_loader import NOTE_NO_CATALOG, ColumnLineageLoader
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql
from sqlglot.errors import SqlglotError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

FCT = "model.p.fct_orders"
ENRICHED = "model.p.int_orders__enriched"
BUILD_WAIT_SECONDS = 5.0


class _FakeBuilder:
    """Stands in for ColumnLineageBuilder: counts the builds, can be held up or made to fail."""

    builds = 0
    fail = False
    started = threading.Event()
    release = threading.Event()
    seen: tuple[Mapping[str, Sequence[str]], Mapping[str, str | None]] | None = None

    def __init__(self, graph: ManifestGraph, columns_by_node: Mapping[str, Sequence[str]], **_: object) -> None:
        self._columns_by_node = columns_by_node

    def build(self, compiled_sql: Mapping[str, str | None]) -> ColumnLineage:
        type(self).started.set()
        assert type(self).release.wait(BUILD_WAIT_SECONDS)
        type(self).builds += 1
        type(self).seen = (self._columns_by_node, compiled_sql)
        if type(self).fail:
            raise SqlglotError("boom")

        return ColumnLineage({}, [], {})


def _node(*columns: str) -> dict[str, Any]:
    return {"columns": {name: {"name": name, "index": i} for i, name in enumerate(columns, start=1)}}


def _catalog(**nodes: dict[str, Any]) -> dict[str, Any]:
    return {"nodes": {f"model.p.{name}": raw for name, raw in nodes.items()}, "sources": {}}


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def fake_builder(monkeypatch: pytest.MonkeyPatch) -> type[_FakeBuilder]:
    _FakeBuilder.builds = 0
    _FakeBuilder.fail = False
    _FakeBuilder.seen = None
    _FakeBuilder.started = threading.Event()
    _FakeBuilder.release = threading.Event()
    _FakeBuilder.release.set()
    monkeypatch.setattr(column_lineage_loader, "ColumnLineageBuilder", _FakeBuilder)
    return _FakeBuilder


@pytest.fixture
def graph(manifest: dict[str, Any]) -> ManifestGraph:
    return ManifestGraph.from_manifest(manifest)


@pytest.fixture
def node_sql() -> NodeSql:
    fake = create_autospec(NodeSql, instance=True)
    fake.compiled.side_effect = lambda node: f"select * from {node.name}"
    return fake


@pytest.fixture
def manifests(graph: ManifestGraph) -> ManifestLoader:
    fake = create_autospec(ManifestLoader, instance=True)
    fake.load.return_value = graph
    return fake


@pytest.fixture
def loader(tmp_path: Path, manifests: ManifestLoader, node_sql: NodeSql) -> ColumnLineageLoader:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(_catalog(fct_orders=_node("b", "a"))), encoding="utf-8")
    return ColumnLineageLoader(tmp_path, manifests, node_sql)


@pytest.mark.usefixtures("fake_builder")
class TestColumnLineageLoader:
    def test_builds_once_per_artifacts_change(
        self, tmp_path: Path, loader: ColumnLineageLoader, fake_builder: type[_FakeBuilder]
    ) -> None:
        first = loader.load()
        again = loader.load()

        assert again is first
        assert fake_builder.builds == 1

        _bump_mtime(tmp_path / "catalog.json")
        second = loader.load()

        assert second is not first
        assert fake_builder.builds == 2

        _bump_mtime(tmp_path / "manifest.json")
        loader.load()

        assert fake_builder.builds == 3

    def test_reads_catalog_columns_in_index_order_and_the_sql_of_models_and_snapshots_only(
        self, tmp_path: Path, loader: ColumnLineageLoader, fake_builder: type[_FakeBuilder]
    ) -> None:
        raw = _catalog(fct_orders={"columns": {"a": {"name": "a", "index": 2}, "b": {"name": "b", "index": 1}}})
        raw["sources"] = {"source.p.shop.orders": _node("id")}
        (tmp_path / "catalog.json").write_text(json.dumps(raw), encoding="utf-8")

        loader.load()

        assert fake_builder.seen is not None
        columns, compiled = fake_builder.seen
        assert columns[FCT] == ["b", "a"]
        assert columns["source.p.shop.orders"] == ["id"]
        assert FCT in compiled
        assert "snapshot.p.snap_customers" in compiled
        assert "seed.p.countries" not in compiled
        assert "source.p.shop.orders" not in compiled

    def test_concurrent_first_calls_build_once(
        self, loader: ColumnLineageLoader, fake_builder: type[_FakeBuilder]
    ) -> None:
        fake_builder.release.clear()
        results: list[ColumnLineage] = []
        threads = [threading.Thread(target=lambda: results.append(loader.load())) for _ in range(2)]

        for thread in threads:
            thread.start()
        assert fake_builder.started.wait(BUILD_WAIT_SECONDS)
        fake_builder.release.set()
        for thread in threads:
            thread.join(BUILD_WAIT_SECONDS)

        assert fake_builder.builds == 1
        assert len(results) == 2
        assert results[0] is results[1]

    def test_keeps_the_last_lineage_when_a_rebuild_fails(
        self, tmp_path: Path, loader: ColumnLineageLoader, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = loader.load()
        catalog = tmp_path / "catalog.json"
        catalog.write_text('{"nodes": {"model.p.fct', encoding="utf-8")  # half-written by a running build
        _bump_mtime(catalog)

        with caplog.at_level("WARNING"):
            kept = loader.load()

        assert kept is first
        assert "stays in use" in caplog.text

        catalog.write_text(json.dumps(_catalog(fct_orders=_node("a"))), encoding="utf-8")  # the build finished
        _bump_mtime(catalog)

        assert loader.load() is not first

    def test_keeps_the_last_lineage_when_the_builder_fails(
        self, tmp_path: Path, loader: ColumnLineageLoader, fake_builder: type[_FakeBuilder]
    ) -> None:
        first = loader.load()
        fake_builder.fail = True
        _bump_mtime(tmp_path / "manifest.json")

        assert loader.load() is first

    def test_raises_when_it_never_built_one(self, tmp_path: Path, node_sql: NodeSql) -> None:
        manifests = create_autospec(ManifestLoader, instance=True)
        manifests.load.side_effect = CatalogUnavailable("no manifest")
        loader = ColumnLineageLoader(tmp_path, manifests, node_sql)

        with pytest.raises(CatalogUnavailable, match="no manifest"):
            loader.load()

    def test_a_catalog_of_the_wrong_shape_raises_when_it_never_built_one(
        self, tmp_path: Path, loader: ColumnLineageLoader
    ) -> None:
        (tmp_path / "catalog.json").write_text("[1, 2]", encoding="utf-8")

        with pytest.raises(CatalogUnavailable, match=r"catalog.json"):
            loader.load()


def test_falls_back_to_yaml_columns_without_a_catalog(tmp_path: Path, manifests: ManifestLoader) -> None:
    sql = {
        FCT: 'select id from "warehouse"."analytics"."int_orders__enriched"',
        ENRICHED: 'select id from "warehouse"."analytics"."stg_shop__orders"',
    }
    node_sql = create_autospec(NodeSql, instance=True)
    node_sql.compiled.side_effect = lambda node: sql.get(node.unique_id)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    loader = ColumnLineageLoader(tmp_path, manifests, node_sql)

    lineage = loader.load()

    assert [info.ref.column for info in lineage.columns_of(FCT)] == ["id"]
    assert lineage.node_note(FCT) == NOTE_NO_CATALOG
    assert ColumnRef(unique_id=ENRICHED, column="id") in lineage.focus(ColumnRef(unique_id=FCT, column="id")).upstream


@pytest.mark.usefixtures("fake_builder")
def test_a_first_build_that_fails_in_sqlglot_is_catalog_unavailable(
    loader: ColumnLineageLoader, fake_builder: type[_FakeBuilder]
) -> None:
    fake_builder.fail = True

    with pytest.raises(CatalogUnavailable, match="cannot build the column lineage"):
        loader.load()
