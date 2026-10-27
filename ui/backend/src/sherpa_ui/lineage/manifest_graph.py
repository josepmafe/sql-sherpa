from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable, KeysView, Mapping

MODEL_FOLDER_INDEX = 1  # a model's fqn is [package, folder under models/, ...]
DEFAULT_SEVERITY = "error"
_NODE_SECTIONS = ("nodes", "sources", "exposures")
_GRAPH_RESOURCE_TYPES = frozenset({"model", "seed", "snapshot", "source", "exposure"})
_MATERIALIZED_TYPES = frozenset({"model", "seed", "snapshot"})


class Layer(StrEnum):
    # The declaration order is the left-to-right order of the graph.
    SOURCE = auto()
    SEED = auto()
    STAGING = auto()
    SNAPSHOT = auto()
    INTERMEDIATE = auto()
    MARTS = auto()
    EXPOSURE = auto()
    OTHER = auto()


_RESOURCE_LAYERS = {
    "source": Layer.SOURCE,
    "seed": Layer.SEED,
    "snapshot": Layer.SNAPSHOT,
    "exposure": Layer.EXPOSURE,
}
_MODEL_FOLDER_LAYERS = {
    "staging": Layer.STAGING,
    "intermediate": Layer.INTERMEDIATE,
    "marts": Layer.MARTS,
}


class NodeColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str | None
    description: str


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    name: str
    resource_type: str
    layer: Layer
    materialization: str | None
    domain: str | None
    description: str
    file_path: str
    package_name: str
    source_name: str | None  # the source a source table belongs to
    raw_sql: str | None
    contract_enforced: bool
    columns: tuple[NodeColumn, ...]
    parents: tuple[str, ...]


class NodeTest(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    name: str
    kind: Literal["generic", "singular", "unit"]
    column: str | None
    severity: Literal["error", "warn"]
    counts_against: tuple[str, ...]


class ManifestGraph:
    """The lineage graph of a dbt manifest (sources, seeds, models, snapshots, exposures) and the tests on it."""

    def __init__(
        self,
        nodes: Mapping[str, GraphNode],
        tests: Iterable[NodeTest],
        generated_at: datetime,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None:
        self.nodes = nodes
        self.generated_at = generated_at
        self.reserved_names = reserved_names  # package, folder and test names: dbt selects groups with them
        self._children: dict[str, list[str]] = {unique_id: [] for unique_id in nodes}
        for node in nodes.values():
            for parent in node.parents:
                self._children[parent].append(node.unique_id)

        self._tests: dict[str, list[NodeTest]] = {unique_id: [] for unique_id in nodes}
        for test in tests:
            for unique_id in test.counts_against:
                self._tests[unique_id].append(test)

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> ManifestGraph:
        raw_nodes = {
            unique_id: raw
            for section in _NODE_SECTIONS
            for unique_id, raw in manifest.get(section, {}).items()
            if raw["resource_type"] in _GRAPH_RESOURCE_TYPES
        }
        nodes = {unique_id: _build_node(raw, raw_nodes.keys()) for unique_id, raw in raw_nodes.items()}
        tests = [_build_test(raw, nodes) for raw in manifest["nodes"].values() if raw["resource_type"] == "test"]
        tests += [_build_unit_test(raw, nodes) for raw in manifest.get("unit_tests", {}).values()]
        generated_at = datetime.fromisoformat(manifest["metadata"]["generated_at"])
        return cls(nodes, tests, generated_at, _reserved_names(manifest, {node.name for node in nodes.values()}))

    def parents(self, unique_id: str) -> tuple[str, ...]:
        return self.nodes[unique_id].parents

    def children(self, unique_id: str) -> tuple[str, ...]:
        return tuple(self._children[unique_id])

    def tests_of(self, unique_id: str) -> tuple[NodeTest, ...]:
        return tuple(self._tests[unique_id])

    def layers(self) -> tuple[Layer, ...]:
        present = {node.layer for node in self.nodes.values()}
        return tuple(layer for layer in Layer if layer in present)


def _build_node(raw: dict[str, Any], graph_ids: KeysView[str]) -> GraphNode:
    resource_type = raw["resource_type"]
    config = raw.get("config", {})
    meta = {**raw.get("meta", {}), **config.get("meta", {})}
    depends_on = raw.get("depends_on", {}).get("nodes", [])
    return GraphNode(
        unique_id=raw["unique_id"],
        name=raw["name"],
        resource_type=resource_type,
        layer=_layer(resource_type, raw.get("fqn", [])),
        materialization=config.get("materialized") if resource_type in _MATERIALIZED_TYPES else None,
        domain=meta.get("domain"),
        description=raw.get("description", ""),
        file_path=raw["original_file_path"].replace("\\", "/"),  # dbt writes the separator of the machine it ran on
        package_name=raw["package_name"],
        source_name=raw.get("source_name") if resource_type == "source" else None,
        raw_sql=raw.get("raw_code") or None,
        contract_enforced=bool(raw.get("contract", {}).get("enforced", False)),
        columns=tuple(
            NodeColumn(name=name, data_type=column.get("data_type"), description=column.get("description", ""))
            for name, column in raw.get("columns", {}).items()
        ),
        parents=tuple(dict.fromkeys(unique_id for unique_id in depends_on if unique_id in graph_ids)),
    )


def _reserved_names(manifest: dict[str, Any], graph_names: set[str]) -> frozenset[str]:
    """What dbt reads as a group or a test in a bare atom: the package and folders of every non-source node (the fqn
    without the node's own name), and the names of the nodes that are not in the graph (tests, analyses...)."""
    raws = [*manifest["nodes"].values(), *manifest.get("exposures", {}).values()]
    groups = {part for raw in raws for part in raw.get("fqn", [])[:-1]}
    others = {raw["name"] for raw in raws if raw["resource_type"] not in _GRAPH_RESOURCE_TYPES}
    others |= {raw["name"] for raw in manifest.get("unit_tests", {}).values()}
    return frozenset(groups | others) - graph_names


def _layer(resource_type: str, fqn: list[str]) -> Layer:
    if resource_type in _RESOURCE_LAYERS:
        return _RESOURCE_LAYERS[resource_type]

    folder = fqn[MODEL_FOLDER_INDEX] if len(fqn) > MODEL_FOLDER_INDEX else ""
    return _MODEL_FOLDER_LAYERS.get(folder, Layer.OTHER)


def _build_test(raw: dict[str, Any], nodes: Mapping[str, GraphNode]) -> NodeTest:
    if "test_metadata" in raw:  # a generic test counts against the node it is defined on
        kind: Literal["generic", "singular"] = "generic"
        counts_against = tuple(unique_id for unique_id in [raw.get("attached_node")] if unique_id in nodes)
    else:
        kind = "singular"
        counts_against = tuple(unique_id for unique_id in raw["depends_on"]["nodes"] if unique_id in nodes)
    return NodeTest(
        unique_id=raw["unique_id"],
        name=raw["name"],
        kind=kind,
        column=raw.get("column_name"),
        severity="warn" if str(raw.get("config", {}).get("severity", DEFAULT_SEVERITY)).lower() == "warn" else "error",
        counts_against=counts_against,
    )


def _build_unit_test(raw: dict[str, Any], nodes: Mapping[str, GraphNode]) -> NodeTest:
    return NodeTest(
        unique_id=raw["unique_id"],
        name=raw["name"],
        kind="unit",
        column=None,
        severity="error",
        counts_against=tuple(
            unique_id
            for unique_id in raw["depends_on"]["nodes"]
            if unique_id in nodes and nodes[unique_id].name == raw["model"]
        ),
    )
