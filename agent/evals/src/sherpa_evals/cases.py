"""The gold set: its schema and the static checks every case must pass (agent spec §7.1)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Literal, Self

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sherpa_mcp.catalog import MARTS_SCHEMA
from sherpa_mcp.sql_guard import DIALECT, SqlGuard
from sqlglot import exp
from sqlglot.errors import SqlglotError

if TYPE_CHECKING:
    from pathlib import Path

Category = Literal["values", "metrics", "grain", "time", "complex", "unanswerable", "clarification"]
ExpectedOutcome = Literal["answered", "unanswerable", "needs_clarification"]
CaseStatus = Literal["draft", "reviewed", "accepted"]

ID_PATTERN: Final = re.compile(r"^(?P<category>[a-z]+)-\d{3}$")
HASH_PATTERN: Final = re.compile(r"[0-9a-fA-F]{32}")
CLOCK_PROBLEM: Final = "reads the clock ({name}); write dates, or use (select max(order_date) from marts.fct_orders)"
HASH_PROBLEM: Final = "holds a 32-hex literal; hashes change with every eval-db rebuild"
ORDER_PROBLEM: Final = "ordered or limited without ORDER BY"
TABLES_PROBLEM: Final = "tables lists {listed}, the SQL reads {read}"
# sqlglot parses now() as CURRENT_TIMESTAMP.
_CLOCK_NODES: Final[tuple[tuple[type[exp.Expr], str], ...]] = (
    (exp.CurrentTimestamp, "now/current_timestamp"),
    (exp.CurrentDate, "current_date"),
)


class Review(BaseModel):
    model_config = ConfigDict(frozen=True)

    reviewer: Literal["codex", "gemini", "opus", "human"]  # opus: the spec's fresh stand-in session
    verdict: Literal["accept", "fix", "reject"]
    notes: str


class GoldCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    category: Category
    question: str
    tranche: Literal[1, 2]
    expected_outcome: ExpectedOutcome
    gold_sql: str | None
    ordered: bool = False
    allow_empty: bool = False
    reconcile_sql: str | None = None
    rationale: str
    tables: tuple[str, ...] = ()
    status: CaseStatus = "draft"
    reviews: tuple[Review, ...] = ()

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        match = ID_PATTERN.match(self.id)
        if match is None or match["category"] != self.category:
            raise ValueError(f"id {self.id!r} must look like '{self.category}-001'")

        if self.expected_outcome == "answered":
            if not self.gold_sql:
                raise ValueError("an answered case needs gold_sql")
            if not self.tables:
                raise ValueError("an answered case needs tables")
        else:
            if self.gold_sql is not None:
                raise ValueError("only an answered case has gold_sql")
            if self.reconcile_sql is not None:
                raise ValueError("only an answered case has reconcile_sql")
            if self.tables:
                raise ValueError("only an answered case has tables")

        return self


class GoldSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    gold_version: int
    dataset_version: int
    cases: tuple[GoldCase, ...]

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        ids = [case.id for case in self.cases]
        duplicate_ids = sorted({i for i in ids if ids.count(i) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate id: {duplicate_ids}")

        questions = [case.question for case in self.cases]
        duplicate_questions = sorted({q for q in questions if questions.count(q) > 1})
        if duplicate_questions:
            raise ValueError(f"duplicate question: {duplicate_questions}")

        return self

    @classmethod
    def load(cls, path: Path) -> GoldSet:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def accepted(self) -> tuple[GoldCase, ...]:
        return tuple(case for case in self.cases if case.status == "accepted")


def static_problems(case: GoldCase, visible_marts: frozenset[str]) -> list[str]:
    """Problems of a case's SQL that need no database. An unanswerable case has no SQL, so none."""
    if case.gold_sql is None:
        return []

    result = SqlGuard(visible_marts).check(case.gold_sql)
    problems = list(result.errors)

    statement = _parse(case.gold_sql)
    if statement is not None:
        problems.extend(CLOCK_PROBLEM.format(name=name) for name in _clock_names(statement))

    if any(_has_hash(sql) for sql in (case.gold_sql, case.reconcile_sql) if sql is not None):
        problems.append(HASH_PROBLEM)

    unordered = statement is not None and statement.args.get("order") is None
    limited = statement is not None and statement.args.get("limit") is not None
    if unordered and (case.ordered or limited):
        problems.append(ORDER_PROBLEM)

    if result.ok:
        read = {table.removeprefix(f"{MARTS_SCHEMA}.") for table in result.tables}
        if read != set(case.tables):
            problems.append(TABLES_PROBLEM.format(listed=_show(set(case.tables)), read=_show(read)))

    return problems


def _parse(sql: str) -> exp.Expr | None:
    try:
        return sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return None


def _clock_names(statement: exp.Expr) -> list[str]:
    names = [name for node in statement.walk() for kind, name in _CLOCK_NODES if isinstance(node, kind)]
    return list(dict.fromkeys(names))


def _has_hash(sql: str) -> bool:
    statement = _parse(sql)
    if statement is None:
        return False

    return any(HASH_PATTERN.search(literal.name) for literal in statement.find_all(exp.Literal) if literal.is_string)


def _show(names: set[str]) -> str:
    return "{" + ", ".join(repr(name) for name in sorted(names)) + "}"
