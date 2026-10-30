from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import StrEnum, auto
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.schema import MappingSchema

from sherpa_ui.lineage.manifest_graph import EPHEMERAL

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from sherpa_ui.lineage.manifest_graph import GraphNode, ManifestGraph

logger = logging.getLogger(__name__)

DIALECT = "postgres"
EPHEMERAL_CTE_PREFIX = "__dbt__cte__"
EPHEMERAL_DATABASE = "__ephemeral__"  # the pseudo-relation an ephemeral model gets: database, schema and identifier
UNKNOWN_TYPE = "unknown"  # the schema only needs column names
SNAPSHOT_GENERATED_COLUMNS = frozenset(
    {"dbt_scd_id", "dbt_updated_at", "dbt_valid_from", "dbt_valid_to", "dbt_is_deleted"}
)
NOTE_SQL_MISSING = "the compiled SQL is not in the dbt target"
NOTE_SQL_UNPARSED = "the compiled SQL did not parse"
NOTE_NOT_IN_OUTPUT = "not in the compiled SQL's output"
NOTE_SELF_READ = "also reads its own previous rows (incremental)"

type _RelationKey = tuple[str, str, str]  # lower-cased (database, schema, identifier)


class ColumnKind(StrEnum):
    PASSTHROUGH = auto()
    RENAME = auto()
    TRANSFORMED = auto()
    ROW_COUNT = auto()
    CONSTANT = auto()
    GENERATED = auto()
    SOURCE = auto()


class Resolution(StrEnum):
    FULL = auto()
    PARTIAL = auto()
    UNRESOLVED = auto()


class ColumnRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    column: str


class ColumnEdge(BaseModel):
    """Shaped like a row of dbt's own `column_lineage` table, so the source of the edges could be swapped."""

    model_config = ConfigDict(frozen=True)

    parent: ColumnRef
    child: ColumnRef


class ColumnInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ColumnRef
    kind: ColumnKind
    resolution: Resolution
    notes: tuple[str, ...]
    counts: str | None  # the unique_id a row_count column counts


class ColumnFocus(BaseModel):
    model_config = ConfigDict(frozen=True)

    focus: ColumnRef
    upstream: frozenset[ColumnRef]
    downstream: frozenset[ColumnRef]
    edges: tuple[ColumnEdge, ...]  # every edge between two columns of the closure


class ColumnLineage:
    """The column-level graph of a project: what each column is and which columns it is made of."""

    def __init__(
        self,
        columns: Mapping[str, tuple[ColumnInfo, ...]],
        edges: Sequence[ColumnEdge],
        node_notes: Mapping[str, str],
    ) -> None:
        self._columns = columns
        self._infos = {info.ref: info for infos in columns.values() for info in infos}
        self._edges = tuple(edges)
        self._node_notes = node_notes
        self._parents: dict[ColumnRef, list[ColumnRef]] = {}
        self._children: dict[ColumnRef, list[ColumnRef]] = {}
        for edge in self._edges:
            self._parents.setdefault(edge.child, []).append(edge.parent)
            self._children.setdefault(edge.parent, []).append(edge.child)

    def columns_of(self, unique_id: str) -> tuple[ColumnInfo, ...]:
        return self._columns.get(unique_id, ())

    def info(self, ref: ColumnRef) -> ColumnInfo | None:
        return self._infos.get(ref)

    def node_note(self, unique_id: str) -> str | None:
        return self._node_notes.get(unique_id)

    def focus(self, ref: ColumnRef) -> ColumnFocus:
        upstream = _reach(ref, self._parents)
        downstream = _reach(ref, self._children)
        closure = upstream | downstream | {ref}
        edges = tuple(edge for edge in self._edges if edge.parent in closure and edge.child in closure)
        return ColumnFocus(focus=ref, upstream=upstream, downstream=downstream, edges=edges)


class ColumnLineageBuilder:
    """Reads the compiled SQL of every model and snapshot with sqlglot and links each output column to its inputs."""

    def __init__(
        self, graph: ManifestGraph, columns_by_node: Mapping[str, Sequence[str]], *, columns_note: str | None = None
    ) -> None:
        self._graph = graph
        self._columns_by_node = columns_by_node
        self._columns_note = columns_note  # about where the columns came from; goes on every node that has none yet

    def build(self, compiled_sql: Mapping[str, str | None]) -> ColumnLineage:
        run = _Run(self._graph, self._columns_by_node, compiled_sql)
        for unique_id in TopologicalSorter(
            {uid: node.parents for uid, node in self._graph.nodes.items()}
        ).static_order():
            run.add(self._graph.nodes[unique_id])

        if self._columns_note is not None:
            for unique_id, infos in run.columns.items():
                if infos:
                    run.node_notes.setdefault(unique_id, self._columns_note)

        return ColumnLineage(run.columns, list(run.edges), run.node_notes)


@dataclass(frozen=True)
class _Leaf:
    """One table column a lineage tree ends in."""

    key: _RelationKey
    column: str


class _Run:
    """The state of one `build`: the schema grows as ephemeral models are read, so it is not shared between builds."""

    def __init__(
        self, graph: ManifestGraph, columns_by_node: Mapping[str, Sequence[str]], compiled_sql: Mapping[str, str | None]
    ) -> None:
        self._graph = graph
        self._columns_by_node = columns_by_node
        self._compiled_sql = compiled_sql
        self.columns: dict[str, tuple[ColumnInfo, ...]] = {}
        self.edges: dict[ColumnEdge, None] = {}  # a dict for the order, without duplicates
        self.node_notes: dict[str, str] = {}
        self._catalog_names: dict[str, dict[str, str]] = {}  # unique_id -> lower-cased column -> catalog column
        self._uid_by_relation: dict[_RelationKey, str] = {}
        self._schema = MappingSchema(dialect=DIALECT)
        for node in graph.nodes.values():
            self._register(node, columns_by_node.get(node.unique_id, ()))

    def add(self, node: GraphNode) -> None:
        match node.resource_type:
            case "model" | "snapshot":
                self._add_query_node(node)
            case "source" | "seed":
                self.columns[node.unique_id] = tuple(
                    _info(node.unique_id, name, ColumnKind.SOURCE)
                    for name in self._columns_by_node.get(node.unique_id, ())
                )

    def _register(self, node: GraphNode, columns: Sequence[str]) -> None:
        key = _pseudo_key(node.name) if node.materialization == EPHEMERAL else _relation_key(node)
        if key is None:
            return

        self._uid_by_relation[key] = node.unique_id
        self._catalog_names[node.unique_id] = {name.lower(): name for name in columns}
        if columns:
            self._schema.add_table(_table(key), dict.fromkeys(columns, UNKNOWN_TYPE), dialect=DIALECT)

    def _add_query_node(self, node: GraphNode) -> None:
        sql = self._compiled_sql.get(node.unique_id)
        if sql is None:
            self._fail(node, NOTE_SQL_MISSING)
            return

        try:
            tree = _parse(sql)
            if tree is None:
                self._fail(node, NOTE_SQL_UNPARSED)
                return

            infos = self._describe(node, self._trace(tree))
        except RecursionError:
            logger.warning("column lineage of %s: the compiled SQL is nested too deeply", node.unique_id)
            self._fail(node, NOTE_SQL_UNPARSED)
            return
        except SqlglotError as exc:
            logger.warning("column lineage of %s: sqlglot could not trace the compiled SQL: %s", node.unique_id, exc)
            self._fail(node, NOTE_SQL_UNPARSED)
            return

        if node.materialization == EPHEMERAL:
            self._learn_ephemeral(node, infos)
        self.columns[node.unique_id] = tuple(infos)

    def _fail(self, node: GraphNode, note: str) -> None:
        self.node_notes[node.unique_id] = note
        names = self._columns_by_node.get(node.unique_id, ())
        self.columns[node.unique_id] = tuple(
            _generated(node.unique_id, name)
            if _is_generated(node, name)
            else _info(node.unique_id, name, ColumnKind.TRANSFORMED, Resolution.UNRESOLVED, (note,))
            for name in names
        )

    def _trace(self, tree: exp.Query) -> dict[str, Node]:
        """The lineage root of every output column. `trim_selects=False` keeps the CTEs reachable from a node's
        source; partial qualification lets a column the parent lacks fail alone instead of failing the query."""
        traced = lineage(
            None, tree, schema=self._schema, dialect=DIALECT, trim_selects=False, allow_partial_qualification=True
        )
        return {name.lower(): root for name, root in traced.items()}

    def _describe(self, node: GraphNode, roots: dict[str, Node]) -> list[ColumnInfo]:
        names = self._columns_by_node.get(node.unique_id) or [name for name in roots if name != "*"]
        infos = []
        for name in names:
            key = name.lower()
            if _is_generated(node, name):
                infos.append(_generated(node.unique_id, name))
            elif key in roots:
                infos.append(self._classify(node, name, roots[key]))
            elif "*" in roots:
                note = f"* is not expanded: {'; '.join(self._unknown_relations(roots['*']))}"
                infos.append(_info(node.unique_id, name, ColumnKind.TRANSFORMED, Resolution.UNRESOLVED, (note,)))
            else:
                infos.append(
                    _info(node.unique_id, name, ColumnKind.TRANSFORMED, Resolution.UNRESOLVED, (NOTE_NOT_IN_OUTPUT,))
                )
        return infos

    def _classify(self, node: GraphNode, name: str, root: Node) -> ColumnInfo:
        leaves = list(_leaves(root))
        notes: list[str] = []
        resolved = 0
        for leaf in leaves:
            parent_uid = self._uid_by_relation.get(leaf.key)
            if parent_uid is None:
                notes.append(f"reads an unknown relation {'.'.join(leaf.key)}")
            elif parent_uid == node.unique_id:
                notes.append(NOTE_SELF_READ)
                resolved += 1
            elif (parent_column := self._catalog_names[parent_uid].get(leaf.column.lower())) is None:
                notes.append(f"column {leaf.column} not found in {self._graph.nodes[parent_uid].name}")
            else:
                self.edges[ColumnEdge(parent=_ref(parent_uid, parent_column), child=_ref(node.unique_id, name))] = None
                resolved += 1
        problems = [note for note in dict.fromkeys(notes) if note != NOTE_SELF_READ]
        resolution = Resolution.FULL if not problems else Resolution.PARTIAL if resolved else Resolution.UNRESOLVED
        kind, kind_notes, counts = self._kind(name, root, leaves)
        return ColumnInfo(
            ref=_ref(node.unique_id, name),
            kind=kind,
            resolution=resolution,
            notes=tuple(dict.fromkeys([*kind_notes, *notes])),
            counts=counts,
        )

    def _kind(self, name: str, root: Node, leaves: list[_Leaf]) -> tuple[ColumnKind, list[str], str | None]:
        inputs = {(leaf.key, leaf.column.lower()) for leaf in leaves}
        if not inputs:
            counted = _counted_relation(root)
            if counted is None:
                return ColumnKind.CONSTANT, [], None
            return ColumnKind.ROW_COUNT, [], self._uid_by_relation.get(counted)

        casts: list[str] = []
        if len(inputs) == 1 and _is_bare_chain(root, casts):
            only_column = next(iter(inputs))[1]
            kind = ColumnKind.PASSTHROUGH if name.lower() == only_column else ColumnKind.RENAME
            return kind, [f"cast to {cast}" for cast in dict.fromkeys(casts)], None
        return ColumnKind.TRANSFORMED, [], None

    def _unknown_relations(self, root: Node) -> list[str]:
        return [
            f"reads an unknown relation {'.'.join(leaf.key)}"
            for leaf in _leaves(root)
            if leaf.key not in self._uid_by_relation
        ]

    def _learn_ephemeral(self, node: GraphNode, infos: list[ColumnInfo]) -> None:
        # The children of an ephemeral model see its output columns, which only its own SQL tells.
        if self._columns_by_node.get(node.unique_id):
            return

        columns = [info.ref.column for info in infos]
        self._catalog_names[node.unique_id] = {name.lower(): name for name in columns}
        self._schema.add_table(_table(_pseudo_key(node.name)), dict.fromkeys(columns, UNKNOWN_TYPE), dialect=DIALECT)


def _parse(sql: str) -> exp.Query | None:
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except SqlglotError:
        return None

    return _repoint_ephemeral_ctes(tree) if isinstance(tree, exp.Query) else None


def _reach(start: ColumnRef, adjacency: Mapping[ColumnRef, list[ColumnRef]]) -> frozenset[ColumnRef]:
    seen: set[ColumnRef] = set()
    queue = deque([start])
    while queue:
        for neighbour in adjacency.get(queue.popleft(), ()):
            if neighbour not in seen and neighbour != start:
                seen.add(neighbour)
                queue.append(neighbour)
    return frozenset(seen)


def _ref(unique_id: str, column: str) -> ColumnRef:
    return ColumnRef(unique_id=unique_id, column=column)


def _info(
    unique_id: str,
    column: str,
    kind: ColumnKind,
    resolution: Resolution = Resolution.FULL,
    notes: tuple[str, ...] = (),
) -> ColumnInfo:
    return ColumnInfo(ref=_ref(unique_id, column), kind=kind, resolution=resolution, notes=notes, counts=None)


def _generated(unique_id: str, column: str) -> ColumnInfo:
    return _info(unique_id, column, ColumnKind.GENERATED)


def _is_generated(node: GraphNode, column: str) -> bool:
    return node.resource_type == "snapshot" and column.lower() in SNAPSHOT_GENERATED_COLUMNS


def _relation_key(node: GraphNode) -> _RelationKey | None:
    relation = node.relation
    if relation is None:
        return None

    return (relation.database.lower(), relation.schema_name.lower(), relation.identifier.lower())


def _pseudo_key(name: str) -> _RelationKey:
    return (EPHEMERAL_DATABASE, EPHEMERAL_DATABASE, name.lower())


def _table(key: _RelationKey) -> exp.Table:
    database, schema, identifier = key
    return exp.Table(
        this=exp.to_identifier(identifier), db=exp.to_identifier(schema), catalog=exp.to_identifier(database)
    )


def _repoint_ephemeral_ctes(tree: exp.Query) -> exp.Query:
    """Cut dbt's `__dbt__cte__<name>` CTEs out and point their references at the ephemeral model's pseudo-relation,
    so the walk stops at the model's direct parents."""
    for cte in list(tree.find_all(exp.CTE)):
        if cte.alias.startswith(EPHEMERAL_CTE_PREFIX):
            cte.pop()
    for with_ in list(tree.find_all(exp.With)):
        if not with_.expressions:
            with_.pop()
    for table in list(tree.find_all(exp.Table)):
        if not table.db and table.name.startswith(EPHEMERAL_CTE_PREFIX):
            pseudo = _table(_pseudo_key(table.name.removeprefix(EPHEMERAL_CTE_PREFIX)))
            pseudo.set("alias", table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name)))
            table.replace(pseudo)
    return tree


def _leaves(root: Node) -> Iterator[_Leaf]:
    """The table columns a lineage tree ends in (a leaf of any other kind, such as `count(*)`, reads no column)."""
    seen: set[tuple[_RelationKey, str]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.downstream:
            stack.extend(reversed(node.downstream))
        elif isinstance(node.source, exp.Table):
            key = (node.source.catalog.lower(), node.source.db.lower(), node.source.name.lower())
            column = _unquote(node.name.partition(".")[2] or node.name)  # the node is named `<table alias>.<column>`
            if (key, column.lower()) not in seen:
                seen.add((key, column.lower()))
                yield _Leaf(key=key, column=column)


def _is_bare_chain(node: Node, casts: list[str]) -> bool:
    """Whether every step from the output down to the table is the bare column or a cast of it."""
    if isinstance(node.source, exp.Table):
        return True

    expression = node.expression.unalias()
    while isinstance(expression, exp.Cast):
        casts.append(_type_text(expression.to))
        expression = expression.this
    return (
        isinstance(expression, exp.Column)
        and bool(node.downstream)
        and all(_is_bare_chain(child, casts) for child in node.downstream)
    )


def _counted_relation(root: Node) -> _RelationKey | None:
    """For a column made of `count(*)`: the relation in the FROM of the select that counts."""
    leaf = root
    while leaf.downstream:
        leaf = leaf.downstream[0]
    counts = [count for count in leaf.expression.find_all(exp.Count) if isinstance(count.this, exp.Star)]
    select = leaf.source
    if not counts or not isinstance(select, exp.Select):
        return None

    return _from_relation(select)


def _from_relation(select: exp.Select) -> _RelationKey | None:
    from_ = select.args.get("from_")
    table = from_.this if from_ else None
    if not isinstance(table, exp.Table):
        return None

    if table.db:
        return (table.catalog.lower(), table.db.lower(), table.name.lower())
    for cte in select.root().find_all(exp.CTE):  # a bare name is a CTE: count what it reads
        if cte.alias.lower() == table.name.lower() and isinstance(cte.this, exp.Select):
            return _from_relation(cte.this)
    return None


def _unquote(identifier: str) -> str:
    if len(identifier) > 1 and identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier


def _type_text(data_type: exp.DataType) -> str:
    """A type as the project writes it: sqlglot renders Postgres' `numeric(12,2)` as `DECIMAL(12, 2)`."""
    text = data_type.sql(dialect=DIALECT).lower().replace(", ", ",")
    return f"numeric{text.removeprefix('decimal')}" if text.startswith("decimal") else text
