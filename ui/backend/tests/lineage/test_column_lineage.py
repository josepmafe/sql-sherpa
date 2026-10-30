from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlglot
from sherpa_ui.lineage import column_lineage
from sherpa_ui.lineage.column_lineage import (
    ColumnEdge,
    ColumnInfo,
    ColumnKind,
    ColumnLineage,
    ColumnLineageBuilder,
    ColumnRef,
    Resolution,
)
from sherpa_ui.lineage.manifest_graph import GraphNode, Layer, ManifestGraph, Relation
from sqlglot import exp
from sqlglot.errors import OptimizeError

if TYPE_CHECKING:
    from collections.abc import Mapping

SRC = "source.p.src"
SEED = "seed.p.countries"
STG = "model.p.stg"
MART = "model.p.mart"
TOP = "model.p.top"
EPH = "model.p.eph"
INC = "model.p.inc"
SNAP = "snapshot.p.snap"
SRC_SQL = '"db"."raw"."src"'
STG_SQL = '"db"."analytics"."stg"'
NOT_IN_TARGET = "the compiled SQL is not in the dbt target"
DID_NOT_PARSE = "the compiled SQL did not parse"


class _Spec:
    """A node and its catalog columns."""

    def __init__(
        self,
        name: str,
        columns: tuple[str, ...],
        kind: str = "model",
        parents: tuple[str, ...] = (),
        mat: str = "table",
    ) -> None:
        self.columns = columns
        self.node = GraphNode(
            unique_id=f"{kind}.p.{name}",
            name=name,
            resource_type=kind,
            layer=Layer.MARTS,
            materialization=None if kind == "source" else mat,
            domain=None,
            description="",
            file_path="models/x.sql",
            package_name="p",
            source_name=None,
            raw_sql=None,
            contract_enforced=False,
            columns=(),
            parents=parents,
            relation=None
            if mat == "ephemeral"
            else Relation(database="db", schema="raw" if kind in {"source", "seed"} else "analytics", identifier=name),
        )


def _lineage(specs: list[_Spec], sql: Mapping[str, str | None]) -> ColumnLineage:
    graph = ManifestGraph({spec.node.unique_id: spec.node for spec in specs}, [], datetime(2026, 10, 27, tzinfo=UTC))
    columns = {spec.node.unique_id: spec.columns for spec in specs}
    return ColumnLineageBuilder(graph, columns).build(sql)


def _src() -> _Spec:
    return _Spec("src", ("id", "amount", "ts"), kind="source")


def _stg(*columns: str) -> _Spec:
    return _Spec("stg", columns, parents=(SRC,))


def _only_column(select: str, column: str = "c") -> tuple[ColumnLineage, ColumnInfo]:
    lineage = _lineage([_src(), _stg(column)], {STG: f"select {select} from {SRC_SQL}"})
    info = lineage.info(_ref(STG, column))
    assert info is not None
    return lineage, info


def _ref(unique_id: str, column: str) -> ColumnRef:
    return ColumnRef(unique_id=unique_id, column=column)


class TestColumnLineageBuilder:
    def test_a_source_or_seed_column_is_a_source_kind(self) -> None:
        lineage = _lineage([_src(), _Spec("countries", ("code",), kind="seed")], {})

        assert [(info.kind, info.resolution) for info in lineage.columns_of(SRC)] == [
            (ColumnKind.SOURCE, Resolution.FULL)
        ] * 3
        assert lineage.columns_of(SEED)[0].kind is ColumnKind.SOURCE

    def test_a_bare_column_is_a_passthrough(self) -> None:
        lineage, info = _only_column("id", column="id")

        assert (info.kind, info.resolution, info.notes) == (ColumnKind.PASSTHROUGH, Resolution.FULL, ())
        assert lineage.focus(info.ref).edges == (ColumnEdge(parent=_ref(SRC, "id"), child=_ref(STG, "id")),)

    def test_a_different_name_is_a_rename(self) -> None:
        lineage, info = _only_column("id as c")

        assert info.kind is ColumnKind.RENAME
        assert lineage.focus(info.ref).upstream == {_ref(SRC, "id")}

    def test_a_cast_of_one_column_is_a_passthrough_with_a_note(self) -> None:
        _, same_name = _only_column("cast(id as bigint) as id", column="id")
        _, renamed = _only_column("amount::numeric(12,2) as c")

        assert (same_name.kind, same_name.notes) == (ColumnKind.PASSTHROUGH, ("cast to bigint",))
        assert (renamed.kind, renamed.notes) == (ColumnKind.RENAME, ("cast to numeric(12,2)",))

    def test_an_expression_is_transformed(self) -> None:
        lineage, info = _only_column("amount * 2 as c")

        assert info.kind is ColumnKind.TRANSFORMED
        assert lineage.focus(info.ref).upstream == {_ref(SRC, "amount")}

    def test_two_inputs_are_transformed(self) -> None:
        lineage, info = _only_column("id || amount as c")

        assert info.kind is ColumnKind.TRANSFORMED
        assert lineage.focus(info.ref).upstream == {_ref(SRC, "id"), _ref(SRC, "amount")}

    def test_a_transform_inside_a_cte_is_not_a_passthrough(self) -> None:
        sql = f"with a as (select upper(id) as id from {SRC_SQL}) select id from a"

        lineage = _lineage([_src(), _stg("id")], {STG: sql})

        assert lineage.columns_of(STG)[0].kind is ColumnKind.TRANSFORMED

    def test_count_star_is_a_row_count_of_its_from_relation(self) -> None:
        lineage, info = _only_column("count(*) as c")

        assert (info.kind, info.counts, info.resolution) == (ColumnKind.ROW_COUNT, SRC, Resolution.FULL)
        assert lineage.focus(info.ref).upstream == frozenset()

    def test_count_star_through_a_cte_counts_the_relation_under_it(self) -> None:
        sql = f"with a as (select id from {SRC_SQL}) select count(*) as c from a"

        lineage = _lineage([_src(), _stg("c")], {STG: sql})

        assert lineage.columns_of(STG)[0].counts == SRC

    def test_a_literal_is_a_constant(self) -> None:
        _, info = _only_column("1 as c")

        assert (info.kind, info.counts, info.resolution) == (ColumnKind.CONSTANT, None, Resolution.FULL)

    def test_snapshot_bookkeeping_columns_are_generated(self) -> None:
        snapshot = _Spec("snap", ("id", "dbt_scd_id", "dbt_valid_to"), kind="snapshot", parents=(SRC,))

        lineage = _lineage([_src(), snapshot], {SNAP: f"select id from {SRC_SQL}"})

        assert [(info.ref.column, info.kind, info.resolution) for info in lineage.columns_of(SNAP)] == [
            ("id", ColumnKind.PASSTHROUGH, Resolution.FULL),
            ("dbt_scd_id", ColumnKind.GENERATED, Resolution.FULL),
            ("dbt_valid_to", ColumnKind.GENERATED, Resolution.FULL),
        ]

    def test_an_ephemeral_parent_is_its_own_node(self) -> None:
        sql = {
            EPH: f"select id as order_id from {SRC_SQL}",
            MART: f"with __dbt__cte__eph as (select id as order_id from {SRC_SQL}) "
            "select e.order_id as oid from __dbt__cte__eph as e",
        }
        specs = [_src(), _Spec("eph", (), parents=(SRC,), mat="ephemeral"), _Spec("mart", ("oid",), parents=(EPH,))]

        lineage = _lineage(specs, sql)

        assert [info.ref.column for info in lineage.columns_of(EPH)] == ["order_id"]
        focus = lineage.focus(_ref(MART, "oid"))
        assert focus.upstream == {_ref(EPH, "order_id"), _ref(SRC, "id")}
        assert set(focus.edges) == {
            ColumnEdge(parent=_ref(EPH, "order_id"), child=_ref(MART, "oid")),
            ColumnEdge(parent=_ref(SRC, "id"), child=_ref(EPH, "order_id")),
        }

    def test_an_ephemeral_reference_without_an_alias_resolves(self) -> None:
        sql = {
            EPH: f"select id as order_id from {SRC_SQL}",
            MART: "with __dbt__cte__eph as (select 1 as order_id) select order_id from __dbt__cte__eph",
        }
        specs = [
            _src(),
            _Spec("eph", (), parents=(SRC,), mat="ephemeral"),
            _Spec("mart", ("order_id",), parents=(EPH,)),
        ]

        lineage = _lineage(specs, sql)

        assert lineage.focus(_ref(MART, "order_id")).upstream == {_ref(EPH, "order_id"), _ref(SRC, "id")}

    def test_star_expands_from_the_schema(self) -> None:
        mart = _Spec("mart", ("id", "amount", "ts"), parents=(SRC,))

        lineage = _lineage([_src(), mart], {MART: f"select * from {SRC_SQL}"})

        assert [(info.ref.column, info.kind, info.resolution) for info in lineage.columns_of(MART)] == [
            (name, ColumnKind.PASSTHROUGH, Resolution.FULL) for name in ("id", "amount", "ts")
        ]
        assert lineage.focus(_ref(MART, "amount")).upstream == {_ref(SRC, "amount")}

    def test_star_over_an_unknown_relation_is_unresolved(self) -> None:
        lineage = _lineage([_src(), _stg("id")], {STG: 'select * from "db"."raw"."mystery"'})

        info = lineage.columns_of(STG)[0]
        assert (info.resolution, info.notes) == (
            Resolution.UNRESOLVED,
            ("* is not expanded: reads an unknown relation db.raw.mystery",),
        )

    def test_quoted_and_unquoted_identifiers_meet(self) -> None:
        mart = _Spec("mart", ("a", "b"), parents=(SRC,))
        sql = {MART: 'select SRC.ID as a, src."amount" as b from DB.Raw."src"'}

        lineage = _lineage([_src(), mart], sql)

        assert lineage.focus(_ref(MART, "a")).upstream == {_ref(SRC, "id")}
        assert lineage.focus(_ref(MART, "b")).upstream == {_ref(SRC, "amount")}

    def test_an_incremental_self_read_is_a_note_not_a_failure(self) -> None:
        inc = _Spec("inc", ("id",), parents=(SRC,), mat="incremental")
        sql = {INC: f'select id from {SRC_SQL} union all select id from "db"."analytics"."inc"'}

        lineage = _lineage([_src(), inc], sql)

        info = lineage.columns_of(INC)[0]
        assert (info.resolution, info.notes) == (Resolution.FULL, ("also reads its own previous rows (incremental)",))
        assert lineage.focus(info.ref).upstream == {_ref(SRC, "id")}
        assert lineage.focus(info.ref).edges == (ColumnEdge(parent=_ref(SRC, "id"), child=_ref(INC, "id")),)

    def test_an_unknown_relation_is_noted(self) -> None:
        mart = _Spec("mart", ("both", "only_unknown", "fine"), parents=(SRC,))
        sql = {
            MART: "select coalesce(a.id, b.x) as both, b.x as only_unknown, a.id as fine "
            f'from {SRC_SQL} as a join "db"."raw"."mystery" as b on true'
        }

        lineage = _lineage([_src(), mart], sql)

        both, only_unknown, fine = lineage.columns_of(MART)
        note = ("reads an unknown relation db.raw.mystery",)
        assert (both.resolution, both.notes) == (Resolution.PARTIAL, note)
        assert (only_unknown.resolution, only_unknown.notes) == (Resolution.UNRESOLVED, note)
        assert (fine.resolution, fine.notes) == (Resolution.FULL, ())
        assert lineage.focus(both.ref).upstream == {_ref(SRC, "id")}

    def test_a_column_the_parent_lacks_leaves_the_other_columns_alone(self) -> None:
        mart = _Spec("mart", ("bad", "good"), parents=(SRC,))

        lineage = _lineage([_src(), mart], {MART: f"select nope as bad, id as good from {SRC_SQL}"})

        bad, good = lineage.columns_of(MART)
        assert bad.resolution is Resolution.UNRESOLVED
        assert "nope" in bad.notes[0]
        assert (good.kind, good.resolution) == (ColumnKind.RENAME, Resolution.FULL)

    def test_a_qualified_column_the_parent_lacks_is_partial_and_the_rest_stay_full(self) -> None:
        mart = _Spec("mart", ("good", "mixed", "lone"), parents=(SRC,))
        sql = {MART: f"select a.id as good, coalesce(a.id, a.nope) as mixed, a.nope as lone from {SRC_SQL} as a"}

        lineage = _lineage([_src(), mart], sql)

        good, mixed, lone = lineage.columns_of(MART)
        assert (good.resolution, good.notes) == (Resolution.FULL, ())
        assert (mixed.resolution, mixed.notes) == (Resolution.PARTIAL, ("column nope not found in src",))
        assert lineage.focus(mixed.ref).upstream == {_ref(SRC, "id")}
        assert (lone.resolution, lone.notes) == (Resolution.UNRESOLVED, ("column nope not found in src",))

    def test_a_stale_column_in_a_join_condition_does_not_fail_the_node(self) -> None:
        mart = _Spec("mart", ("id",), parents=(SRC,))
        sql = {MART: f"select a.id from {SRC_SQL} as a join {SRC_SQL} as b on a.id = b.nope"}

        lineage = _lineage([_src(), mart], sql)

        assert (lineage.columns_of(MART)[0].resolution, lineage.node_note(MART)) == (Resolution.FULL, None)

    def test_count_star_in_a_cte_reading_another_cte_is_a_row_count(self) -> None:
        sql = (
            f"with a as (select * from {SRC_SQL}), t as (select id, count(*) as c from a group by id) select t.c from t"
        )

        lineage = _lineage([_src(), _stg("c")], {STG: sql})

        info = lineage.columns_of(STG)[0]
        assert (info.kind, info.counts) == (ColumnKind.ROW_COUNT, SRC)

    def test_a_column_missing_from_the_compiled_output_is_noted(self) -> None:
        lineage = _lineage([_src(), _stg("id", "ghost")], {STG: f"select id from {SRC_SQL}"})

        id_info, ghost = lineage.columns_of(STG)
        assert id_info.resolution is Resolution.FULL
        assert (ghost.resolution, ghost.notes) == (Resolution.UNRESOLVED, ("not in the compiled SQL's output",))

    @pytest.mark.parametrize("compiled", ["select from from", "", "   "])
    def test_unparseable_sql_marks_only_its_node_unresolved(self, compiled: str) -> None:
        mart = _Spec("mart", ("id",), parents=(STG,))
        sql = {STG: compiled, MART: f"select id from {STG_SQL}"}

        lineage = _lineage([_src(), _stg("id"), mart], sql)

        assert lineage.columns_of(STG)[0].resolution is Resolution.UNRESOLVED
        assert lineage.node_note(STG) == DID_NOT_PARSE
        assert lineage.columns_of(MART)[0].resolution is Resolution.FULL
        assert lineage.node_note(MART) is None

    def test_a_recursion_error_marks_only_its_node_unresolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_parse_one = sqlglot.parse_one

        def explode_for_stg(sql: str, *args: object, **kwargs: object) -> object:
            if "analytics" not in sql:  # only the staging node reads straight from the source
                raise RecursionError
            return real_parse_one(sql, *args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr(sqlglot, "parse_one", explode_for_stg)
        mart = _Spec("mart", ("id",), parents=(STG,))

        lineage = _lineage(
            [_src(), _stg("id"), mart], {STG: f"select id from {SRC_SQL}", MART: f"select id from {STG_SQL}"}
        )

        assert lineage.columns_of(STG)[0].resolution is Resolution.UNRESOLVED
        assert lineage.node_note(STG) == DID_NOT_PARSE
        assert lineage.columns_of(MART)[0].resolution is Resolution.FULL
        assert lineage.node_note(MART) is None

    def test_a_trace_time_sqlglot_error_marks_only_its_node_unresolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_lineage = column_lineage.lineage

        def fail_for_stg(column: object, tree: exp.Query, *args: object, **kwargs: object) -> object:
            if "raw" in tree.sql():  # only the staging node reads straight from the source
                raise OptimizeError("Ambiguous column: id")
            return real_lineage(column, tree, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(column_lineage, "lineage", fail_for_stg)
        mart = _Spec("mart", ("id",), parents=(STG,))
        sql = {STG: f"select id from {SRC_SQL}", MART: f"select id from {STG_SQL}"}

        lineage = _lineage([_src(), _stg("id"), mart], sql)

        assert lineage.columns_of(STG)[0].resolution is Resolution.UNRESOLVED
        assert lineage.node_note(STG) == DID_NOT_PARSE
        assert lineage.columns_of(MART)[0].resolution is Resolution.FULL
        assert lineage.node_note(MART) is None

    def test_missing_sql_marks_the_node_unresolved_with_its_note(self) -> None:
        lineage = _lineage([_src(), _stg("id", "amount")], {STG: None})

        assert [info.resolution for info in lineage.columns_of(STG)] == [Resolution.UNRESOLVED] * 2
        assert lineage.node_note(STG) == NOT_IN_TARGET

    def test_a_model_absent_from_the_sql_mapping_counts_as_missing(self) -> None:
        lineage = _lineage([_src(), _stg("id")], {})

        assert lineage.node_note(STG) == NOT_IN_TARGET


def _chain() -> ColumnLineage:
    """src.id -> stg.id -> mart.id -> top.id, plus src.amount -> stg.amount (a side branch)."""
    specs = [
        _src(),
        _stg("id", "amount"),
        _Spec("mart", ("id",), parents=(STG,)),
        _Spec("top", ("id",), parents=(MART,)),
    ]
    sql = {
        STG: f"select id, amount from {SRC_SQL}",
        MART: f"select id from {STG_SQL}",
        TOP: 'select id from "db"."analytics"."mart"',
    }
    return _lineage(specs, sql)


class TestColumnLineage:
    def test_focus_walks_both_ways(self) -> None:
        focus = _chain().focus(_ref(MART, "id"))

        assert focus.focus == _ref(MART, "id")
        assert focus.upstream == {_ref(STG, "id"), _ref(SRC, "id")}
        assert focus.downstream == {_ref(TOP, "id")}

    def test_focus_edges_are_those_inside_the_closure(self) -> None:
        focus = _chain().focus(_ref(MART, "id"))

        assert set(focus.edges) == {
            ColumnEdge(parent=_ref(SRC, "id"), child=_ref(STG, "id")),
            ColumnEdge(parent=_ref(STG, "id"), child=_ref(MART, "id")),
            ColumnEdge(parent=_ref(MART, "id"), child=_ref(TOP, "id")),
        }

    def test_focus_of_a_source_column_is_only_downstream(self) -> None:
        focus = _chain().focus(_ref(SRC, "amount"))

        assert focus.upstream == frozenset()
        assert focus.downstream == {_ref(STG, "amount")}

    def test_focus_of_an_unknown_column_is_just_itself(self) -> None:
        focus = _chain().focus(_ref(STG, "nope"))

        assert (focus.upstream, focus.downstream, focus.edges) == (frozenset(), frozenset(), ())

    def test_info_of_an_unknown_column_is_none(self) -> None:
        assert _chain().info(_ref(STG, "nope")) is None
