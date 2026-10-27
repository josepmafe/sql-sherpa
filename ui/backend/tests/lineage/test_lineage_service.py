from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable, LastFullBuild, NodeResult
from sherpa_ui.lineage.lineage_service import LineageService
from sherpa_ui.lineage.manifest_graph import Layer, ManifestGraph
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql
from sherpa_ui.lineage.node_status import NodeStatus, TestOutcome
from sherpa_ui.lineage.selector import UnsupportedSelector

if TYPE_CHECKING:
    from sherpa_ui.lineage.lineage_service import LineageGraph

STARTED = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
PROJECT_NODE_COUNT = 12  # the conftest project: 6 models, seed, snapshot, 3 sources, exposure


def _result(unique_id: str, status: str, resource_type: str = "model") -> NodeResult:
    return NodeResult(
        unique_id=unique_id,
        resource_type=resource_type,
        status=status,
        failures=None,
        execution_time_s=2.5,
        message="done",
    )


def _build(*results: NodeResult) -> LastFullBuild:
    return LastFullBuild(invocation_id="i1", started_at=STARTED, results={r.unique_id: r for r in results})


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


@pytest.fixture
def service(graph: ManifestGraph, builds: BuildStatusReader, node_sql: NodeSql) -> LineageService:
    loader = create_autospec(ManifestLoader, instance=True)
    loader.load.return_value = graph
    return LineageService(loader, builds, node_sql)


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
        builds.last_full_build.return_value = _build(  # type: ignore[attr-defined]
            _result("model.p.stg_shop__orders", "success"),
            _result("model.p.int_orders__enriched", "success"),
            _result("model.p.fct_orders", "error"),
            _result("model.p.dim_customers", "success"),
            _result("test.p.unique_dim_customers_id", "fail", "test"),
            _result("test.p.not_null_fct_orders_order_id", "pass", "test"),
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
        builds.last_full_build.return_value = _build(  # type: ignore[attr-defined]
            _result("model.p.dim_customers", "success"),
            _result("test.p.unique_dim_customers_id", "warn", "test"),
        )

        result = service.graph("", None, failing_only=True)

        assert result.nodes == []
        assert result.edges == []

    def test_build_info_of_a_recorded_build(self, service: LineageService, builds: BuildStatusReader) -> None:
        builds.last_full_build.return_value = _build(_result("model.p.fct_orders", "success"))  # type: ignore[attr-defined]

        result = service.graph("fct_orders", None, failing_only=False)

        assert (result.build.state, result.build.started_at, result.build.node_count) == ("ok", STARTED, 1)
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
        builds.last_full_build.return_value = _build(  # type: ignore[attr-defined]
            _result("model.p.fct_orders", "success"),
            _result("test.p.not_null_fct_orders_order_id", "pass", "test"),
            _result("test.p.assert_orders_match_customers", "warn", "test"),
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
        recorded = _result("model.p.fct_orders", "error").model_copy(update={"message": message})
        builds.last_full_build.return_value = _build(recorded)  # type: ignore[attr-defined]

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
