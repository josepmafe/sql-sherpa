"""The wrapper that sorts and filters every row of a user's query on the server (UI spec §5, server mode)."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Final, Literal, Self

from psycopg import sql
from pydantic import BaseModel, ConfigDict, model_validator
from sherpa_mcp.sql_guard import DIALECT
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlglot.tokens import Token

SortDirection = Literal["asc", "desc"]
FilterOperator = Literal["=", "!=", "<", "<=", ">", ">=", "contains", "is_null", "is_not_null"]

# The newline before `)` keeps a trailing `--` comment in the user's SQL from swallowing it (UI spec §5).
WRAPPER_PREFIX: Final = "select * from (\n"
WRAPPER_SUFFIX: Final = "\n) as q"
COMPARISON_SQL: Final[dict[str, str]] = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
DIRECTION_SQL: Final[dict[str, str]] = {"asc": "asc", "desc": "desc"}
NULL_OPERATORS: Final[frozenset[str]] = frozenset({"is_null", "is_not_null"})
# `contains` is a substring match, so the wildcards of LIKE (and its default escape character) in the value are escaped.
LIKE_ESCAPES: Final = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})
_TRAILING_TERMINATORS: Final = re.compile(r"[\s;]+\Z")
_POSTGRES: Final = Dialect.get_or_raise(DIALECT)


class DuplicateColumnsError(Exception):
    """Two result columns share a name, so `q.<name>` would be ambiguous: sorting and filtering stay in the browser."""


class SortSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    direction: SortDirection


class FilterSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    operator: FilterOperator
    value: str | None = None

    @model_validator(mode="after")
    def check_value(self) -> Self:
        if self.operator in NULL_OPERATORS and self.value is not None:
            raise ValueError(f"operator {self.operator} takes no value")

        if self.operator not in NULL_OPERATORS and self.value is None:
            raise ValueError(f"operator {self.operator} needs a value")

        return self


class WrappedQuery(BaseModel):
    """The server-mode query, its bound parameters, and how many of its characters come before the user's SQL."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    query: sql.Composed
    params: tuple[object, ...]
    prefix_length: int


def build_wrapped_query(
    sql_text: str,
    result_columns: Sequence[str],
    sort: Sequence[SortSpec] = (),
    filters: Sequence[FilterSpec] = (),
) -> WrappedQuery:
    """`select * from (\\n<sql>\\n) as q where … order by …` over the user's SQL.

    Columns are only ever the result's own names (`ValueError` otherwise), quoted as identifiers; values are bound
    parameters. Run it with its parameters even when there are none: psycopg then reads `%%` (which every `%` of the
    user's SQL is doubled to) as `%`, so Postgres gets the user's SQL unchanged and its error positions still hold.
    """
    duplicates = sorted(name for name, count in Counter(result_columns).items() if count > 1)
    if duplicates:
        raise DuplicateColumnsError(f"duplicate column names: {', '.join(duplicates)}")

    known = set(result_columns)
    named = [*(spec.column for spec in sort), *(spec.column for spec in filters)]
    unknown = [column for column in named if column not in known]
    if unknown:
        raise ValueError(f"column {unknown[0]!r} is not a column of the result")

    params: list[object] = []
    parts: list[sql.Composable] = [
        sql.SQL(WRAPPER_PREFIX),
        sql.SQL(strip_terminators(sql_text).replace("%", "%%")),
        sql.SQL(WRAPPER_SUFFIX),
    ]
    if filters:
        conditions = [_condition(spec, params) for spec in filters]
        parts += [sql.SQL(" where "), sql.SQL(" and ").join(conditions)]

    if sort:
        parts += [sql.SQL(" order by "), sql.SQL(", ").join(_order(spec) for spec in sort)]

    return WrappedQuery(query=sql.Composed(parts), params=tuple(params), prefix_length=len(WRAPPER_PREFIX))


def strip_terminators(sql_text: str) -> str:
    """`sql_text` without its trailing `;`s and whitespace.

    A `;` followed only by comments (`select 1; -- note`) is blanked to a space rather than removed, so no character
    moves (Postgres's error positions stay valid in the original text) and the comment stays a comment.
    """
    chars = list(sql_text)
    for token in reversed(_tokens(sql_text)):
        if token.token_type != TokenType.SEMICOLON:
            break

        chars[token.start] = " "

    return _TRAILING_TERMINATORS.sub("", "".join(chars))


def _condition(spec: FilterSpec, params: list[object]) -> sql.Composable:
    """The filter as a condition on `q`; its value, if any, is appended to `params`."""
    column = _column(spec.column)
    match spec.operator:
        case "is_null":
            return sql.SQL("{} is null").format(column)
        case "is_not_null":
            return sql.SQL("{} is not null").format(column)
        case "contains":
            assert spec.value is not None  # FilterSpec.check_value
            params.append(spec.value.translate(LIKE_ESCAPES))
            return sql.SQL("{}::text ilike '%%' || %s || '%%'").format(column)
        case _:
            params.append(spec.value)
            return sql.SQL("{} {} %s").format(column, sql.SQL(COMPARISON_SQL[spec.operator]))


def _order(spec: SortSpec) -> sql.Composable:
    return sql.SQL("{} {}").format(_column(spec.column), sql.SQL(DIRECTION_SQL[spec.direction]))


def _column(name: str) -> sql.Composable:
    return sql.SQL("q.{}").format(sql.Identifier(name))


def _tokens(sql_text: str) -> list[Token]:
    """sqlglot's tokens (the guard's view of the query), or none when it cannot tokenize it (the guard rejects it)."""
    try:
        return _POSTGRES.tokenize(sql_text)
    except SqlglotError:
        return []
