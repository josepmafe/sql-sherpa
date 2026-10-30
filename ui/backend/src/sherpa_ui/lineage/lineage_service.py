from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict
from sherpa_mcp.catalog import CatalogUnavailable

from sherpa_ui.build.build_status import BuildStatusUnavailable
from sherpa_ui.lineage.column_lineage import ColumnKind, ColumnRef, Resolution
from sherpa_ui.lineage.column_lineage_loader import SQL_RESOURCE_TYPES
from sherpa_ui.lineage.manifest_graph import Layer
from sherpa_ui.lineage.node_status import NodeStatus, StatusDeriver, TestOutcome
from sherpa_ui.lineage.selector import SelectorEvaluator

if TYPE_CHECKING:
    from sherpa_ui.build.build_status import BuildStatusReader, LastFullBuild
    from sherpa_ui.lineage.column_lineage import ColumnInfo, ColumnLineage
    from sherpa_ui.lineage.column_lineage_loader import ColumnLineageLoader
    from sherpa_ui.lineage.manifest_graph import GraphNode, ManifestGraph, NodeColumn
    from sherpa_ui.lineage.manifest_loader import ManifestLoader
    from sherpa_ui.lineage.node_sql import NodeSql

logger = logging.getLogger(__name__)

SQL_NOTE: Final = "the compiled SQL is not in the dbt target; run `make build-prod`"


class ColumnView(BaseModel):
    """What the graph shows of a column: its name and how it is made."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: ColumnKind
    resolution: Resolution


class NodeSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    name: str
    resource_type: str
    layer: Layer
    materialization: str | None
    domain: str | None
    status: NodeStatus
    failing_tests: int


class LineageNode(NodeSummary):
    columns: list[ColumnView] | None = None  # None unless the graph was asked for columns and they were available


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
    column_lineage: Literal["ok", "unavailable"] = "ok"  # "unavailable": columns were asked for and cannot be read
    column_lineage_reason: str | None = None


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


class ColumnDetail(BaseModel):
    """A column of the node panel: the YAML's text, plus the trace's verdict when there is one."""

    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str | None
    description: str
    kind: ColumnKind | None
    resolution: Resolution | None
    notes: list[str]
    counts: str | None  # the unique_id a row_count column counts


class NodeDetails(NodeSummary):
    description: str
    file_path: str
    contract_enforced: bool
    raw_sql: str | None
    compiled_sql: str | None
    sql_note: str | None  # set when a model or snapshot has no compiled file
    columns: list[ColumnDetail]
    column_lineage_note: str | None  # about the node's columns as a whole, or why the trace is unavailable
    tests: list[TestInfo]
    build: NodeBuildInfo | None
    is_mart: bool
    selector: str  # what `dbt --select` takes to select this node alone


class FocusRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    node: str
    column: str


class FocusColumn(ColumnView):
    node: str
    direction: Literal["focus", "upstream", "downstream"]
    notes: list[str]


class FocusEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent: FocusRef
    child: FocusRef


class ColumnFocusView(BaseModel):
    """The column, every column it is made of and every column made of it, and the edges between them."""

    model_config = ConfigDict(frozen=True)

    focus: FocusRef
    columns: list[FocusColumn]
    edges: list[FocusEdge]


class LineageService:
    """Blocking: call it from a worker thread."""

    def __init__(
        self, loader: ManifestLoader, builds: BuildStatusReader, node_sql: NodeSql, columns: ColumnLineageLoader
    ) -> None:
        self._loader = loader
        self._builds = builds
        self._node_sql = node_sql
        self._columns = columns

    def graph(
        self, select: str, layers: frozenset[Layer] | None, *, failing_only: bool, columns: bool = False
    ) -> LineageGraph:
        """Raises `UnsupportedSelector` for a selector outside the supported subset. With `columns`, each node
        carries its columns; if they cannot be read the graph is still answered, flagged `unavailable`."""
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
        lineage, reason = self._column_lineage() if columns else (None, None)
        if lineage is not None:
            nodes = [node.model_copy(update={"columns": _column_views(lineage, node.unique_id)}) for node in nodes]
        return LineageGraph(
            nodes=nodes,
            edges=edges,
            build=info,
            layers_available=list(graph.layers()),
            column_lineage="unavailable" if reason is not None else "ok",
            column_lineage_reason=reason,
        )

    def node(self, unique_id: str) -> NodeDetails | None:
        graph = self._loader.load()
        node = graph.nodes.get(unique_id)
        if node is None:
            return None

        _, build = self._build()
        deriver = StatusDeriver(graph, build)
        summary = self._summary(graph, deriver, node)
        lineage, reason = self._column_lineage()
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
            columns=[_column_detail(lineage, node, column) for column in node.columns],
            column_lineage_note=reason or (lineage.node_note(unique_id) if lineage is not None else None),
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

    def column_focus(self, unique_id: str, column: str) -> ColumnFocusView | None:
        """None for a node or column that does not exist. Raises `CatalogUnavailable` without column lineage."""
        if unique_id not in self._loader.load().nodes:
            return None

        lineage = self._columns.load()
        info = _find_column(lineage, unique_id, column)
        if info is None:
            return None

        focus = lineage.focus(info.ref)
        ordered: list[tuple[ColumnRef, Literal["focus", "upstream", "downstream"]]] = [
            *((ref, "upstream") for ref in sorted(focus.upstream, key=_ref_order)),
            (focus.focus, "focus"),
            *((ref, "downstream") for ref in sorted(focus.downstream, key=_ref_order)),
        ]
        return ColumnFocusView(
            focus=_focus_ref(focus.focus),
            columns=[_focus_column(lineage, ref, direction) for ref, direction in ordered],
            edges=[FocusEdge(parent=_focus_ref(edge.parent), child=_focus_ref(edge.child)) for edge in focus.edges],
        )

    def _column_lineage(self) -> tuple[ColumnLineage | None, str | None]:
        """The lineage, or why there is none."""
        try:
            return self._columns.load(), None
        except CatalogUnavailable as exc:
            logger.warning("lineage: column lineage unavailable: %s", exc)
            return None, str(exc)

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
        return LineageNode(**self._summary(graph, deriver, node).model_dump())

    def _summary(self, graph: ManifestGraph, deriver: StatusDeriver, node: GraphNode) -> NodeSummary:
        failing = sum(deriver.test_outcome(test) == TestOutcome.FAILED for test in graph.tests_of(node.unique_id))
        return NodeSummary(
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


def _column_views(lineage: ColumnLineage, unique_id: str) -> list[ColumnView]:
    return [
        ColumnView(name=info.ref.column, kind=info.kind, resolution=info.resolution)
        for info in lineage.columns_of(unique_id)
    ]


def _column_detail(lineage: ColumnLineage | None, node: GraphNode, column: NodeColumn) -> ColumnDetail:
    info = _find_column(lineage, node.unique_id, column.name) if lineage is not None else None
    return ColumnDetail(
        name=column.name,
        data_type=column.data_type,
        description=column.description,
        kind=info.kind if info else None,
        resolution=info.resolution if info else None,
        notes=list(info.notes) if info else [],
        counts=info.counts if info else None,
    )


def _find_column(lineage: ColumnLineage, unique_id: str, column: str) -> ColumnInfo | None:
    """The exact name first, then any casing: the YAML and the catalog may not agree on case."""
    exact = lineage.info(ColumnRef(unique_id=unique_id, column=column))
    if exact is not None:
        return exact

    return next((info for info in lineage.columns_of(unique_id) if info.ref.column.lower() == column.lower()), None)


def _focus_column(
    lineage: ColumnLineage, ref: ColumnRef, direction: Literal["focus", "upstream", "downstream"]
) -> FocusColumn:
    info = lineage.info(ref)
    if info is None:  # every edge joins two columns of the lineage
        raise KeyError(ref)

    return FocusColumn(
        name=ref.column,
        kind=info.kind,
        resolution=info.resolution,
        node=ref.unique_id,
        direction=direction,
        notes=list(info.notes),
    )


def _focus_ref(ref: ColumnRef) -> FocusRef:
    return FocusRef(node=ref.unique_id, column=ref.column)


def _ref_order(ref: ColumnRef) -> tuple[str, str]:
    return ref.unique_id, ref.column
