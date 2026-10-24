"""Static check that a model-written query is one read-only SELECT over the visible marts (agent spec §4.2)."""

from __future__ import annotations

from typing import Final

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import SqlglotError

from sherpa_mcp.catalog import MARTS_SCHEMA

DIALECT: Final = "postgres"
# Agent spec §4.2. Names as sqlglot reports them (`sql_name()`, or the name for an unknown function). Where sqlglot
# parses a Postgres function into a node with another name, that name is listed next to the Postgres one:
# string_agg -> group_concat, bool_and/bool_or -> logical_and/logical_or, var_samp -> variance,
# var_pop -> variance_pop, ceiling -> ceil, pow -> power, char_length -> length, substr -> substring,
# ltrim/rtrim -> trim, strpos/position -> str_position, lpad/rpad -> pad, date_part -> extract,
# date_trunc -> timestamp_trunc, now -> current_timestamp, to_char -> time_to_str, to_date -> str_to_date.
# mod(a, b) parses as the `%` operator (`mod` stays listed for the Postgres spelling).
ALLOWED_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # aggregates
        *("count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop", "variance"),
        *("var_samp", "var_pop", "variance_pop", "percentile_cont", "percentile_disc", "mode"),
        *("string_agg", "group_concat", "array_agg", "bool_and", "bool_or", "logical_and", "logical_or"),
        # window
        *("row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value", "nth_value"),
        *("percent_rank", "cume_dist"),
        # math
        *("abs", "round", "ceil", "ceiling", "floor", "trunc", "power", "pow", "sqrt", "ln", "log", "exp", "mod"),
        *("sign", "greatest", "least"),
        # string
        *("lower", "upper", "length", "char_length", "substring", "substr", "trim", "ltrim", "rtrim", "replace"),
        *("concat", "concat_ws", "left", "right", "split_part", "strpos", "position", "str_position", "lpad"),
        *("rpad", "pad", "initcap"),
        # date
        *("date_trunc", "timestamp_trunc", "date_part", "extract", "age", "now", "current_date"),
        *("current_timestamp", "to_char", "time_to_str", "to_date", "str_to_date", "make_date", "date_bin"),
        # conditional and casts
        *("coalesce", "nullif", "case", "if", "cast", "try_cast"),
        # trigram
        "similarity",
    }
)
# sqlglot models these operators as function nodes; they call no function.
_OPERATOR_NODES: Final = (exp.Connector, exp.Exists)
_QUERY_NODES: Final = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_WRITE_NODES: Final = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Set,
    exp.Copy,
    exp.Command,
)


class GuardResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    errors: tuple[str, ...]
    tables: tuple[str, ...]


class SqlGuard:
    """Never rewrites the SQL and never raises on bad SQL: every problem is an error in the result."""

    def __init__(self, visible_marts: frozenset[str]) -> None:
        self._visible_marts = visible_marts

    def check(self, sql: str) -> GuardResult:
        try:
            parsed = sqlglot.parse(sql, read=DIALECT)
        except (SqlglotError, RecursionError) as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return _rejected(f"could not parse the query: {first_line}")

        # A trailing `;` gives None, a trailing comment an empty Semicolon.
        statements = [stmt for stmt in parsed if stmt is not None and not isinstance(stmt, exp.Semicolon)]
        if len(statements) != 1:
            return _rejected(f"expected exactly one statement, got {len(statements)}")

        statement = statements[0]
        if not isinstance(statement, _QUERY_NODES):
            return _rejected(f"the statement must be a SELECT query, not {_statement_kind(statement)}")

        errors: list[str] = []
        tables: list[str] = []
        for node in statement.walk():
            errors.extend(self._node_errors(node, tables))

        unique_errors = tuple(dict.fromkeys(errors))
        return GuardResult(ok=not unique_errors, errors=unique_errors, tables=tuple(dict.fromkeys(tables)))

    def _node_errors(self, node: exp.Expr, tables: list[str]) -> list[str]:
        match node:
            case exp.Into():
                return ["SELECT ... INTO is not allowed"]
            case exp.Lock():
                return ["locking clause FOR UPDATE/SHARE is not allowed"]
            case _ if isinstance(node, _WRITE_NODES):
                return [f"{_statement_kind(node)} is not allowed"]
            case exp.DataType() if not _is_built_in_type(node):
                return [f"cast to type {node.sql(dialect=DIALECT)} is not allowed"]
            case exp.Dot(expression=exp.Func() as func):
                return [f"function {node.this.sql(dialect=DIALECT)}.{_function_name(func)} is not allowed"]
            case exp.Func() if not isinstance(node, _OPERATOR_NODES):
                name = _function_name(node)
                return [] if name in ALLOWED_FUNCTIONS else [f"function {name} is not allowed"]
            case exp.Table():
                return self._table_errors(node, tables)
            case _:
                return []

    def _table_errors(self, table: exp.Table, tables: list[str]) -> list[str]:
        source = table.this
        if isinstance(source, exp.Func):
            return []  # a table function: checked as a function

        if not isinstance(source, exp.Identifier) or table.args.get("catalog") is not None:
            return [f"table {table.sql(dialect=DIALECT)} is not allowed; use marts.<name>"]

        name = _identifier_value(source)
        schema = table.args.get("db")
        if schema is None:
            if name in _ctes_in_scope(table):
                return []

            return [f"table {name} is not schema-qualified; qualify it as {MARTS_SCHEMA}.{name}"]

        qualified = f"{_identifier_value(schema)}.{name}"
        if _identifier_value(schema) != MARTS_SCHEMA:
            return [f"table {qualified} is outside {MARTS_SCHEMA}"]

        if name not in self._visible_marts:
            return [f"table {qualified} is not a visible mart"]

        tables.append(qualified)
        return []


def _rejected(error: str) -> GuardResult:
    return GuardResult(ok=False, errors=(error,), tables=())


def _statement_kind(statement: exp.Expr) -> str:
    if isinstance(statement, exp.Command):
        return str(statement.this).upper()

    return statement.key.upper()


def _function_name(func: exp.Func) -> str:
    return func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower()


def _is_built_in_type(data_type: exp.DataType) -> bool:
    """sqlglot knows the standard types; reg* types (catalog lookups), domains and qualified types it does not."""
    return isinstance(data_type.this, exp.DataType.Type) and data_type.this != exp.DataType.Type.USERDEFINED


def _identifier_value(identifier: exp.Identifier) -> str:
    """How Postgres resolves the name: unquoted names fold to lower case, quoted ones are kept verbatim."""
    return identifier.this if identifier.quoted else identifier.this.lower()


def _ctes_in_scope(table: exp.Table) -> set[str]:
    """CTE names Postgres would resolve this unqualified table to.

    A query's CTEs are visible in its body and its subqueries. Inside a CTE only the earlier CTEs of the same WITH
    are (plus the CTE itself under RECURSIVE), so a non-recursive `with x as (select * from x)` reads a real table.
    """
    names: set[str] = set()
    child: exp.Expr = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            position = next(index for index, cte in enumerate(node.expressions) if cte is child)
            visible = node.expressions[: position + 1] if node.args.get("recursive") else node.expressions[:position]
            names.update(_cte_name(cte) for cte in visible)
        else:
            names.update(
                _cte_name(cte)
                for value in node.args.values()
                if isinstance(value, exp.With) and value is not child
                for cte in value.expressions
            )
        child, node = node, node.parent

    return names


def _cte_name(cte: exp.CTE) -> str:
    return _identifier_value(cte.args["alias"].this)
