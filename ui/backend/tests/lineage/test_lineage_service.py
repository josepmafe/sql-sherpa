from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable
from sherpa_ui.lineage.column_lineage import (
    ColumnEdge,
    ColumnInfo,
    ColumnKind,
    ColumnLineage,
    ColumnRef,
    Resolution,
)
from sherpa_ui.lineage.column_lineage_loader import ColumnLineageLoader
from sherpa_ui.lineage.lineage_service import ColumnDetail, ColumnView, FocusRef, LineageService
from sherpa_ui.lineage.manifest_graph import Layer, ManifestGraph
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql
from sherpa_ui.lineage.node_status import NodeStatus, TestOutcome
from sherpa_ui.lineage.selector import UnsupportedSelector
from ui_helpers import STARTED_AT, make_last_full_build, make_node_result

if TYPE_CHECKING:
    from sherpa_ui.lineage.lineage_service import LineageGraph

PROJECT_NODE_COUNT = 12  # the conftest project: 6 models, seed, snapshot, 3 sources, exposure


@pytest.fixture
def graph(manifest: dict[str, Any]) -> ManifestGraph:
    return ManifestGraph.from_manifest(manifest)


@pytest.fixture
def builds() -> BuildStatusReader:
    fake = create_autospec(BuildStatusReader, instance=True)
    fake.last_full_build.return_value = None
    return fake


@pytest.fixture
def node_sql() -> NodeSql:
    fake = create_autospec(NodeSql, instance=True)
    fake.compiled.return_value = "select compiled"
    return fake


def _ref(unique_id: str, column: str) -> ColumnRef:
    return ColumnRef(unique_id=unique_id, column=column)


def _info(
    unique_id: str, column: str, kind: ColumnKind, resolution: Resolution = Resolution.FULL, notes: tuple[str, ...] = ()
) -> ColumnInfo:
    return ColumnInfo(ref=_ref(unique_id, column), kind=kind, resolution=resolution, notes=notes, counts=None)


@pytest.fixture
def column_lineage() -> ColumnLineage:
    """stg_shop__orders.id -> int_orders__enriched.id -> fct_orders.id, and fct_orders.id -> dim_customers.id."""
    ids = ["model.p.stg_shop__orders", "model.p.int_orders__enriched", "model.p.fct_orders", "model.p.dim_customers"]
    columns = {
        ids[0]: (_info(ids[0], "id", ColumnKind.RENAME),),
        ids[1]: (_info(ids[1], "id", ColumnKind.PASSTHROUGH),),
        ids[2]: (_info(ids[2], "id", ColumnKind.TRANSFORMED, Resolution.PARTIAL, ("column x not found in y",)),),
        ids[3]: (_info(ids[3], "id", ColumnKind.PASSTHROUGH),),
    }
    edges = [
        ColumnEdge(parent=_ref(ids[0], "id"), child=_ref(ids[1], "id")),
        ColumnEdge(parent=_ref(ids[1], "id"), child=_ref(ids[2], "id")),
        ColumnEdge(parent=_ref(ids[2], "id"), child=_ref(ids[3], "id")),
    ]
    return ColumnLineage(columns, edges, {ids[3]: "a node note"})


@pytest.fixture
def columns_loader(column_lineage: ColumnLineage) -> ColumnLineageLoader:
    fake = create_autospec(ColumnLineageLoader, instance=True)
    fake.load.return_value = column_lineage
    return fake


@pytest.fixture
def service(
    graph: ManifestGraph, builds: BuildStatusReader, node_sql: NodeSql, columns_loader: ColumnLineageLoader
) -> LineageService:
    loader = create_autospec(ManifestLoader, instance=True)
    loader.load.return_value = graph
    return LineageService(loader, builds, node_sql, columns_loader)


def _ids(result: LineageGraph) -> set[str]:
    return {node.unique_id for node in result.nodes}


def _edges(result: LineageGraph) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


class TestLineageServiceGraph:
    def test_empty_selector_is_the_whole_project_with_edges_from_parent_to_child(self, service: LineageService) -> None:
        result = service.graph("", None, failing_only=False)

        assert len(result.nodes) == PROJECT_NODE_COUNT
        assert ("model.p.fct_orders", "model.p.dim_customers") in _edges(result)
        assert ("source.p.shop.orders", "model.p.stg_shop__orders") in _edges(result)

    def test_selector_picks_the_node_set_and_edges_between_them(self, service: LineageService) -> None:
        result = service.graph("+fct_orders", None, failing_only=False)

        assert _ids(result) == {
            "model.p.fct_orders",
            "model.p.int_orders__enriched",
            "model.p.stg_shop__orders",
            "seed.p.countries",
            "source.p.shop.orders",
        }
        assert ("model.p.int_orders__enriched", "model.p.fct_orders") in _edges(result)

    def test_unsupported_selector_propagates(self, service: LineageService) -> None:
        with pytest.raises(UnsupportedSelector):
            service.graph("tag:nightly", None, failing_only=False)

    def test_layers_filter_hides_nodes_and_their_edges_without_bridging(self, service: LineageService) -> None:
        result = service.graph("+dim_customers", frozenset({Layer.MARTS, Layer.STAGING}), failing_only=False)

        assert {node.layer for node in result.nodes} == {Layer.MARTS, Layer.STAGING}
        assert _edges(result) == {("model.p.fct_orders", "model.p.dim_customers")}

    def test_layers_available_ignore_the_filters(self, service: LineageService) -> None:
        result = service.graph("fct_orders", frozenset({Layer.MARTS}), failing_only=False)

        assert result.layers_available == [
            Layer.SOURCE,
            Layer.SEED,
            Layer.STAGING,
            Layer.SNAPSHOT,
            Layer.INTERMEDIATE,
            Layer.MARTS,
            Layer.EXPOSURE,
        ]

    def test_failing_only_keeps_failed_nodes_and_their_mutual_edges(
        self, service: LineageService, builds: BuildStatusReader
    ) -> None:
        builds.last_full_build.return_value = make_last_full_build(  # type: ignore[attr-defined]
            make_node_result("model.p.stg_shop__orders", "success"),
            make_node_result("model.p.int_orders__enriched", "success"),
            make_node_result("model.p.fct_orders", "error"),
            make_node_result("model.p.dim_customers", "success"),
            make_node_result("test.p.unique_dim_customers_id", "fail"),
            make_node_result("test.p.not_null_fct_orders_order_id", "pass"),
        )

        result = service.graph("", None, failing_only=True)

        assert _ids(result) == {"model.p.fct_orders", "model.p.dim_customers"}
        assert _edges(result) == {("model.p.fct_orders", "model.p.dim_customers")}
        assert {node.status for node in result.nodes} == {NodeStatus.FAILED}
        assert {node.unique_id: node.failing_tests for node in result.nodes} == {
            "model.p.fct_orders": 0,
            "model.p.dim_customers": 1,
        }

    def test_failing_only_ignores_warned_nodes(self, service: LineageService, builds: BuildStatusReader) -> None:
        builds.last_full_build.return_value = make_last_full_build(  # type: ignore[attr-defined]
            make_node_result("model.p.dim_customers", "success"),
            make_node_result("test.p.unique_dim_customers_id", "warn"),
        )

        result = service.graph("", None, failing_only=True)

        assert result.nodes == []
        assert result.edges == []

    def test_build_info_of_a_recorded_build(self, service: LineageService, builds: BuildStatusReader) -> None:
        builds.last_full_build.return_value = make_last_full_build(make_node_result("model.p.fct_orders", "success"))  # type: ignore[attr-defined]

        result = service.graph("fct_orders", None, failing_only=False)

        assert (result.build.state, result.build.started_at, result.build.node_count) == ("ok", STARTED_AT, 1)
        assert result.nodes[0].status == NodeStatus.OK

    def test_without_a_build_every_model_has_no_build(self, service: LineageService) -> None:
        result = service.graph("+fct_orders", None, failing_only=False)

        assert (result.build.state, result.build.started_at, result.build.node_count) == ("no_build", None, None)
        assert {node.resource_type: node.status for node in result.nodes} == {
            "model": NodeStatus.NO_BUILD,
            "seed": NodeStatus.NO_BUILD,
            "source": NodeStatus.NONE,
        }

    def test_unavailable_build_status_is_logged(
        self, service: LineageService, builds: BuildStatusReader, caplog: pytest.LogCaptureFixture
    ) -> None:
        builds.last_full_build.side_effect = BuildStatusUnavailable("no grant on ops")  # type: ignore[attr-defined]

        with caplog.at_level(logging.WARNING, logger="sherpa_ui.lineage.lineage_service"):
            service.graph("fct_orders", None, failing_only=False)

        assert "build status unavailable: no grant on ops" in caplog.text

    def test_unavailable_database_degrades_to_the_graph_alone(
        self, service: LineageService, builds: BuildStatusReader
    ) -> None:
        builds.last_full_build.side_effect = BuildStatusUnavailable("down")  # type: ignore[attr-defined]

        failing = service.graph("fct_orders", None, failing_only=True)
        everything = service.graph("fct_orders", None, failing_only=False)

        assert failing.build.state == "unavailable"
        assert failing.nodes == []  # nothing can be called failing without a build
        assert [node.status for node in everything.nodes] == [NodeStatus.NO_BUILD]


class TestLineageServiceNode:
    def test_unknown_id_is_none(self, service: LineageService) -> None:
        assert service.node("model.p.nope") is None

    def test_test_ids_are_not_nodes(self, service: LineageService) -> None:
        assert service.node("test.p.not_null_fct_orders_order_id") is None

    def test_panel_of_a_mart_with_its_build_and_tests(self, service: LineageService, builds: BuildStatusReader) -> None:
        builds.last_full_build.return_value = make_last_full_build(  # type: ignore[attr-defined]
            make_node_result("model.p.fct_orders", "success", execution_time_s=2.5, message="done"),
            make_node_result("test.p.not_null_fct_orders_order_id", "pass"),
            make_node_result("test.p.assert_orders_match_customers", "warn"),
        )

        details = service.node("model.p.fct_orders")

        assert details is not None
        assert (details.name, details.layer, details.domain, details.is_mart) == (
            "fct_orders",
            Layer.MARTS,
            "sales",
            True,
        )
        assert details.contract_enforced is True
        assert details.file_path == "models/marts/fct_orders.sql"
        assert (details.raw_sql, details.compiled_sql, details.sql_note) == (
            "select 1 as fct_orders",
            "select compiled",
            None,
        )
        assert [column.name for column in details.columns] == ["id"]
        assert details.status == NodeStatus.WARNED
        assert details.build is not None
        assert (details.build.status, details.build.execution_time_s, details.build.message) == (
            "success",
            2.5,
            "done",
        )
        assert {(t.name, t.kind, t.severity, t.outcome) for t in details.tests} == {
            ("not_null_fct_orders_order_id", "generic", "error", TestOutcome.PASSED),
            ("assert_orders_match_customers", "singular", "warn", TestOutcome.WARNED),
            ("revenue_is_summed", "unit", "error", TestOutcome.NOT_RUN),
        }

    def test_the_build_message_keeps_only_its_first_line(
        self, service: LineageService, builds: BuildStatusReader
    ) -> None:
        message = (
            "Database Error in model fct_orders\nDETAIL: Failing row contains (null, ana@example.com, 42)\nCONTEXT: x"
        )
        recorded = make_node_result("model.p.fct_orders", "error", message=message)
        builds.last_full_build.return_value = make_last_full_build(recorded)  # type: ignore[attr-defined]

        details = service.node("model.p.fct_orders")

        assert details is not None
        assert details.build is not None
        assert details.build.message == "Database Error in model fct_orders"
        assert "ana@example.com" not in details.model_dump_json()

    @pytest.mark.parametrize(
        ("unique_id", "selector"),
        [
            ("source.p.shop.orders", "source:shop.orders"),
            ("model.p.fct_orders", "fct_orders"),
            ("exposure.p.sales_dashboard", "sales_dashboard"),
        ],
    )
    def test_the_selector_of_a_node_selects_it_alone(
        self, service: LineageService, unique_id: str, selector: str
    ) -> None:
        details = service.node(unique_id)

        assert details is not None
        assert details.selector == selector
        assert _ids(service.graph(details.selector, None, failing_only=False)) == {unique_id}

    def test_missing_compiled_file_keeps_the_raw_sql_with_a_note(
        self, service: LineageService, node_sql: NodeSql
    ) -> None:
        node_sql.compiled.return_value = None  # type: ignore[attr-defined]

        details = service.node("model.p.fct_orders")

        assert details is not None
        assert details.compiled_sql is None
        assert details.raw_sql == "select 1 as fct_orders"
        assert details.sql_note is not None
        assert "make build-prod" in details.sql_note

    def test_a_source_has_no_sql_and_no_note(self, service: LineageService, node_sql: NodeSql) -> None:
        details = service.node("source.p.shop.orders")

        assert details is not None
        assert (details.raw_sql, details.compiled_sql, details.sql_note, details.build) == (None, None, None, None)
        assert details.is_mart is False
        node_sql.compiled.assert_not_called()  # type: ignore[attr-defined]

    def test_unavailable_database_leaves_the_panel_without_a_build(
        self, service: LineageService, builds: BuildStatusReader
    ) -> None:
        builds.last_full_build.side_effect = BuildStatusUnavailable("down")  # type: ignore[attr-defined]

        details = service.node("model.p.fct_orders")

        assert details is not None
        assert (details.status, details.build) == (NodeStatus.NO_BUILD, None)


class TestLineageServiceColumns:
    def test_graph_with_columns_carries_kinds(self, service: LineageService) -> None:
        result = service.graph("fct_orders", None, failing_only=False, columns=True)

        assert result.column_lineage == "ok"
        assert result.column_lineage_reason is None
        [node] = result.nodes
        assert node.columns == [ColumnView(name="id", kind=ColumnKind.TRANSFORMED, resolution=Resolution.PARTIAL)]

    def test_graph_without_the_flag_carries_no_columns_and_never_loads_them(
        self, service: LineageService, columns_loader: ColumnLineageLoader
    ) -> None:
        result = service.graph("fct_orders", None, failing_only=False)

        assert [node.columns for node in result.nodes] == [None]
        columns_loader.load.assert_not_called()  # type: ignore[attr-defined]

    def test_column_lineage_unavailable_keeps_the_graph(
        self, service: LineageService, columns_loader: ColumnLineageLoader
    ) -> None:
        columns_loader.load.side_effect = CatalogUnavailable("no catalog")  # type: ignore[attr-defined]

        result = service.graph("fct_orders", None, failing_only=False, columns=True)

        assert [node.name for node in result.nodes] == ["fct_orders"]
        assert result.column_lineage == "unavailable"
        assert result.column_lineage_reason == "no catalog"
        assert [node.columns for node in result.nodes] == [None]

    def test_node_columns_carry_the_yaml_text_and_the_trace(self, service: LineageService) -> None:
        details = service.node("model.p.fct_orders")

        assert details is not None
        assert details.columns == [
            ColumnDetail(
                name="id",
                data_type="bigint",
                description="The key.",
                kind=ColumnKind.TRANSFORMED,
                resolution=Resolution.PARTIAL,
                notes=["column x not found in y"],
                counts=None,
            )
        ]
        assert details.column_lineage_note is None

    def test_node_note_and_unavailability_reach_the_panel(
        self, service: LineageService, columns_loader: ColumnLineageLoader
    ) -> None:
        noted = service.node("model.p.dim_customers")
        columns_loader.load.side_effect = CatalogUnavailable("no catalog")  # type: ignore[attr-defined]
        unavailable = service.node("model.p.fct_orders")

        assert noted is not None
        assert noted.column_lineage_note == "a node note"
        assert unavailable is not None
        assert unavailable.column_lineage_note == "no catalog"
        assert [(c.name, c.kind, c.resolution) for c in unavailable.columns] == [("id", None, None)]

    def test_column_focus_walks_both_ways_from_the_column(self, service: LineageService) -> None:
        view = service.column_focus("model.p.int_orders__enriched", "id")

        assert view is not None
        assert view.focus == FocusRef(node="model.p.int_orders__enriched", column="id")
        assert [(c.node, c.name, c.direction) for c in view.columns] == [
            ("model.p.stg_shop__orders", "id", "upstream"),
            ("model.p.int_orders__enriched", "id", "focus"),
            ("model.p.dim_customers", "id", "downstream"),
            ("model.p.fct_orders", "id", "downstream"),
        ]
        assert {(e.parent.node, e.child.node) for e in view.edges} == {
            ("model.p.stg_shop__orders", "model.p.int_orders__enriched"),
            ("model.p.int_orders__enriched", "model.p.fct_orders"),
            ("model.p.fct_orders", "model.p.dim_customers"),
        }
        fct = next(c for c in view.columns if c.node == "model.p.fct_orders")
        assert (fct.kind, fct.resolution, fct.notes) == (
            ColumnKind.TRANSFORMED,
            Resolution.PARTIAL,
            ["column x not found in y"],
        )

    def test_column_focus_unknown_column_is_none(self, service: LineageService) -> None:
        assert service.column_focus("model.p.fct_orders", "nope") is None

    def test_column_focus_unknown_node_is_none(self, service: LineageService) -> None:
        assert service.column_focus("model.p.nope", "id") is None

    def test_column_focus_matches_the_column_ignoring_case(self, service: LineageService) -> None:
        view = service.column_focus("model.p.fct_orders", "ID")

        assert view is not None
        assert view.focus.column == "id"
