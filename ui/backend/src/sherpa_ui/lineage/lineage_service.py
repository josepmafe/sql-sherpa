from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict

from sherpa_ui.build.build_status import BuildStatusUnavailable
from sherpa_ui.lineage.manifest_graph import Layer, NodeColumn
from sherpa_ui.lineage.node_status import NodeStatus, StatusDeriver, TestOutcome
from sherpa_ui.lineage.selector import SelectorEvaluator

if TYPE_CHECKING:
    from sherpa_ui.build.build_status import BuildStatusReader, LastFullBuild
    from sherpa_ui.lineage.manifest_graph import GraphNode, ManifestGraph
    from sherpa_ui.lineage.manifest_loader import ManifestLoader
    from sherpa_ui.lineage.node_sql import NodeSql

logger = logging.getLogger(__name__)

SQL_NOTE: Final = "the compiled SQL is not in the dbt target; run `make build-prod`"
SQL_RESOURCE_TYPES: Final = frozenset({"model", "snapshot"})


class LineageNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    name: str
    resource_type: str
    layer: Layer
    materialization: str | None
    domain: str | None
    status: NodeStatus
    failing_tests: int


class LineageEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str  # the parent
    target: str  # the child


class BuildInfo(BaseModel):
    """`no_build`: the warehouse records no full prod build; `unavailable`: it could not be asked."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ok", "no_build", "unavailable"]
    started_at: datetime | None
    node_count: int | None


class LineageGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    nodes: list[LineageNode]
    edges: list[LineageEdge]
    build: BuildInfo
    layers_available: list[Layer]  # the layers of the whole manifest, whatever the filters hide


class TestInfo(BaseModel):
    __test__ = False  # not a pytest class, despite the name
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["generic", "singular", "unit"]
    column: str | None
    severity: Literal["error", "warn"]
    outcome: TestOutcome


class NodeBuildInfo(BaseModel):
    """The node's own row in the last full build; `status` is the word dbt recorded (success, error, skipped...)."""

    model_config = ConfigDict(frozen=True)

    status: str
    execution_time_s: float | None
    message: str | None


class NodeDetails(LineageNode):
    description: str
    file_path: str
    contract_enforced: bool
    raw_sql: str | None
    compiled_sql: str | None
    sql_note: str | None  # set when a model or snapshot has no compiled file
    columns: list[NodeColumn]
    tests: list[TestInfo]
    build: NodeBuildInfo | None
    is_mart: bool
    selector: str  # what `dbt --select` takes to select this node alone


class LineageService:
    """Blocking: call it from a worker thread."""

    def __init__(self, loader: ManifestLoader, builds: BuildStatusReader, node_sql: NodeSql) -> None:
        self._loader = loader
        self._builds = builds
        self._node_sql = node_sql

    def graph(self, select: str, layers: frozenset[Layer] | None, *, failing_only: bool) -> LineageGraph:
        """Raises `UnsupportedSelector` for a selector outside the supported subset."""
        graph = self._loader.load()
        selected = SelectorEvaluator(graph).select(select)
        info, build = self._build()
        deriver = StatusDeriver(graph, build)

        nodes = [
            self._lineage_node(graph, deriver, node)
            for node in graph.nodes.values()
            if node.unique_id in selected and (layers is None or node.layer in layers)
        ]
        if failing_only:
            nodes = [node for node in nodes if node.status == NodeStatus.FAILED]

        kept = {node.unique_id for node in nodes}
        edges = [
            LineageEdge(source=parent, target=node.unique_id)
            for node in nodes
            for parent in graph.nodes[node.unique_id].parents
            if parent in kept
        ]
        return LineageGraph(nodes=nodes, edges=edges, build=info, layers_available=list(graph.layers()))

    def node(self, unique_id: str) -> NodeDetails | None:
        graph = self._loader.load()
        node = graph.nodes.get(unique_id)
        if node is None:
            return None

        _, build = self._build()
        deriver = StatusDeriver(graph, build)
        summary = self._lineage_node(graph, deriver, node)
        has_sql = node.resource_type in SQL_RESOURCE_TYPES
        compiled = self._node_sql.compiled(node) if has_sql else None
        return NodeDetails(
            **summary.model_dump(),
            description=node.description,
            file_path=node.file_path,
            contract_enforced=node.contract_enforced,
            raw_sql=node.raw_sql,
            compiled_sql=compiled,
            sql_note=SQL_NOTE if has_sql and compiled is None else None,
            columns=list(node.columns),
            tests=[
                TestInfo(
                    name=test.name,
                    kind=test.kind,
                    column=test.column,
                    severity=test.severity,
                    outcome=deriver.test_outcome(test),
                )
                for test in graph.tests_of(unique_id)
            ],
            build=self._node_build(build, unique_id),
            is_mart=node.layer == Layer.MARTS,
            selector=_selector(node),
        )

    def _build(self) -> tuple[BuildInfo, LastFullBuild | None]:
        try:
            build = self._builds.last_full_build()
        except BuildStatusUnavailable as exc:
            logger.warning("lineage: build status unavailable: %s", exc)
            return BuildInfo(state="unavailable", started_at=None, node_count=None), None

        if build is None:
            return BuildInfo(state="no_build", started_at=None, node_count=None), None

        return BuildInfo(state="ok", started_at=build.started_at, node_count=build.node_count), build

    def _node_build(self, build: LastFullBuild | None, unique_id: str) -> NodeBuildInfo | None:
        recorded = build.results.get(unique_id) if build is not None else None
        if recorded is None:
            return None

        return NodeBuildInfo(
            status=recorded.status,
            execution_time_s=recorded.execution_time_s,
            message=_first_line(recorded.message),  # the lines after it carry warehouse values (UI spec §6.5)
        )

    def _lineage_node(self, graph: ManifestGraph, deriver: StatusDeriver, node: GraphNode) -> LineageNode:
        failing = sum(deriver.test_outcome(test) == TestOutcome.FAILED for test in graph.tests_of(node.unique_id))
        return LineageNode(
            unique_id=node.unique_id,
            name=node.name,
            resource_type=node.resource_type,
            layer=node.layer,
            materialization=node.materialization,
            domain=node.domain,
            status=deriver.node_status(node.unique_id),
            failing_tests=failing,
        )


def _selector(node: GraphNode) -> str:
    return f"source:{node.source_name}.{node.name}" if node.resource_type == "source" else node.name


def _first_line(message: str | None) -> str | None:
    lines = message.splitlines() if message else []
    return lines[0] if lines else None
