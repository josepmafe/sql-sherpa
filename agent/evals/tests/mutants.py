"""Deliberately wrong answerers for the negative control. Test helpers, not shipped."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import sqlglot
from sherpa_evals.answerers import NO_STATS, Attempt, OracleAnswerer
from sherpa_evals.compare import normalize
from sherpa_mcp.sql_guard import DIALECT
from sqlglot import exp

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.cases import GoldSet
    from sherpa_evals.compare import Result


def drop_cancelled_filter(sql: str) -> str:
    """Remove `not [x.]is_cancelled` and `[x.]is_cancelled = false` from AND chains in WHERE and JOIN ... ON."""
    tree = sqlglot.parse_one(sql, read=DIALECT)
    for condition in list(tree.find_all(exp.Where, exp.Join)):
        holder = condition.this if isinstance(condition, exp.Where) else condition.args.get("on")
        if holder is None:
            continue
        kept = [part for part in _and_parts(holder) if not _is_cancelled_filter(part)]
        if len(kept) == len(_and_parts(holder)):
            continue
        if not kept:
            condition.pop() if isinstance(condition, exp.Where) else condition.set("on", None)
            continue
        condition.set("this" if isinstance(condition, exp.Where) else "on", exp.and_(*kept, copy=False))
    return tree.sql(dialect=DIALECT)


def gross_for_net(sql: str) -> str:
    """Read `gross_revenue` where the SQL reads the column `net_revenue`; output names stay."""
    tree = sqlglot.parse_one(sql, read=DIALECT)
    for column in list(tree.find_all(exp.Column)):
        if column.name != "net_revenue":
            continue
        swapped = column.copy()
        swapped.set("this", exp.to_identifier("gross_revenue"))
        # a bare projection keeps its output name
        column.replace(exp.alias_(swapped, "net_revenue") if isinstance(column.parent, exp.Select) else swapped)
    return tree.sql(dialect=DIALECT)


def flip_order(sql: str) -> str:
    """Invert the direction of every top-level ORDER BY key."""
    tree = sqlglot.parse_one(sql, read=DIALECT)
    order = tree.args.get("order")
    for key in order.expressions if order is not None else []:
        key.set("desc", not key.args.get("desc"))
        key.set("nulls_first", not key.args.get("nulls_first"))
    return tree.sql(dialect=DIALECT)


MUTATIONS: dict[str, Callable[[str], str]] = {
    "drop_cancelled_filter": drop_cancelled_filter,
    "gross_for_net": gross_for_net,
    "flip_order": flip_order,
}


class MutantAnswerer(OracleAnswerer):
    """The oracle, answering with its gold SQL changed by `mutation`."""

    def __init__(self, gold: GoldSet, mutation: Callable[[str], str]) -> None:
        super().__init__(gold)
        self._mutation = mutation
        self.name = "mutant"

    async def attempt(self, question: str) -> Attempt:
        attempt = await super().attempt(question)
        if attempt.sql is None:
            return attempt
        return attempt.model_copy(update={"sql": self._mutation(attempt.sql)})


class AlwaysAnsweredAnswerer:
    name = "always_answered"

    async def attempt(self, question: str) -> Attempt:
        return Attempt(outcome="answered", sql="select 1 as x", reason=None, stats=NO_STATS)


def identical(gold: Result, other: Result, *, ordered: bool) -> bool:
    """Naive on purpose: same columns in the same order, same normalised rows."""
    if gold.columns != other.columns:
        return False
    gold_rows = [tuple(normalize(cell) for cell in row) for row in gold.rows]
    other_rows = [tuple(normalize(cell) for cell in row) for row in other.rows]
    return gold_rows == other_rows if ordered else Counter(gold_rows) == Counter(other_rows)


def _and_parts(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.And):
        return [*_and_parts(node.left), *_and_parts(node.right)]
    return [node]


def _is_cancelled_filter(node: exp.Expr) -> bool:
    match node:
        case exp.Not(this=exp.Column() as column):
            return column.name == "is_cancelled"
        case exp.EQ(this=exp.Column() as column, expression=exp.Boolean(this=False)):
            return column.name == "is_cancelled"
        case _:
            return False


def changes(mutation: Callable[[str], str], sql: str) -> bool:
    """Whether the mutation changes the query, apart from sqlglot's re-rendering of it."""
    return mutation(sql) != sqlglot.parse_one(sql, read=DIALECT).sql(dialect=DIALECT)
