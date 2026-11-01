"""Static check that a model-written query is one read-only SELECT over the visible marts (agent spec §4.2)."""

from __future__ import annotations

import re
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
# ltrim/rtrim -> trim, strpos/position -> str_position, date_part -> extract,
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
        *("concat", "concat_ws", "left", "right", "split_part", "strpos", "position", "str_position", "initcap"),
        # date
        *("date_trunc", "timestamp_trunc", "date_part", "extract", "age", "now", "current_date"),
        *("current_timestamp", "to_char", "time_to_str", "to_date", "str_to_date", "make_date", "date_bin"),
        # conditional and casts
        *("coalesce", "nullif", "case", "if", "cast", "try_cast"),
        # trigram
        "similarity",
    }
)
# Agent spec §4.2 item 2: casts go to this closed list. A cast to another type (json, uuid, bytea, char, bit, the reg*
# types, domains, ...) is rejected. The walk visits an array's element type too, so `jsonb[]` fails on JSONB.
CAST_TYPES: Final[frozenset[exp.DType]] = frozenset(
    {
        *(exp.DType.SMALLINT, exp.DType.INT, exp.DType.BIGINT, exp.DType.DECIMAL, exp.DType.FLOAT, exp.DType.DOUBLE),
        *(exp.DType.BOOLEAN, exp.DType.TEXT, exp.DType.VARCHAR, exp.DType.DATE, exp.DType.TIME, exp.DType.TIMESTAMP),
        *(exp.DType.TIMESTAMPTZ, exp.DType.INTERVAL, exp.DType.ARRAY),
    }
)
CAST_HINT: Final = (
    "cast to smallint, integer, bigint, numeric, real, double precision, boolean, text, varchar, date, time, "
    "timestamp, timestamptz, interval or an array of these"
)
ERROR_SNIPPET_LENGTH: Final = 60  # longest expression an error quotes for an operator the guard rejects
# Every error starts with `[guard:<rule>]`, a stable code a caller can match on; the rest may quote the query.
ERROR_CODE: Final = "[guard:{rule}] "
# One-line remedies for rejected functions the model reaches for most often, by the name as written.
FUNCTION_HINTS: Final[dict[str, str]] = {
    "generate_series": "use marts.dim_date for a series of dates",
    "to_timestamp": "use to_char, make_date or date_trunc",
    "grouping": "use a plain GROUP BY, or one query per grouping level",
}
JSON_FUNCTION_PREFIXES: Final = ("json", "to_json", "row_to_json", "array_to_json")
JSON_HINT: Final = "aggregate with string_agg, or select the columns"
DEFAULT_FUNCTION_HINT: Final = "rewrite it with the common aggregate, window, string, date and math functions"
DEFAULT_OPERATOR_HINT: Final = "use comparison operators, LIKE/ILIKE or IN (...)"
PARAMETER_MESSAGE: Final = "query parameters ($1, ?, :name) and the prefix @ operator are not allowed"
PARAMETER_HINT: Final = "write the values into the query, and abs(x) for an absolute value"
# Each SELECT (CTE body, subquery, union arm) is a planning step: a few hundred chained CTEs outrun the 5 s timeout
# in the planner, before any row (agent spec §4.2 item 3).
MAX_QUERY_SCOPES: Final = 50
_MARTS_HINT: Final = "query only the marts that list_tables returns"
_OPERATOR_HINTS: Final[tuple[tuple[type[exp.Expr], str], ...]] = (
    (exp.Array, "use IN (...) instead of = ANY(ARRAY[...])"),
    (exp.RegexpLike, "use LIKE or ILIKE instead of a regular expression"),
    (exp.RegexpILike, "use LIKE or ILIKE instead of a regular expression"),
)
# Allowed calls that read the real clock, named as the model may have written them (sqlglot parses `now()` as
# CURRENT_TIMESTAMP); the warehouse's "today" is its as-of date (agent spec §4.1).
_CLOCK_CALLS: Final[tuple[tuple[type[exp.Expr], str], ...]] = (
    (exp.CurrentTimestamp, "now()/current_timestamp"),
    (exp.CurrentDate, "current_date"),
)
_CALL_HEAD: Final = re.compile(r"([A-Za-z_]\w*)\s*\(")
_WRITTEN_NAME: Final = re.compile(r"[A-Za-z_]\w*")
# sqlglot models these operators as function nodes; they call no function.
_OPERATOR_NODES: Final = (exp.Connector, exp.Exists)
# The SQL keyword for the statements whose sqlglot node is named otherwise (the model reads this name in the error).
_STATEMENT_KEYWORDS: Final = {"transaction": "BEGIN", "truncatetable": "TRUNCATE"}
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
    clock_calls: tuple[str, ...] = ()  # "now()/current_timestamp", "current_date"; not errors


class SqlGuard:
    """Never rewrites the SQL and never raises on bad SQL: every problem is an error in the result."""

    def __init__(self, visible_marts: frozenset[str], allowed_functions: frozenset[str] = ALLOWED_FUNCTIONS) -> None:
        """`visible_marts`: the names of the marts the agent may read, as Postgres stores them.

        `allowed_functions` defaults to the agent's list; another caller (the UI) may pass its own, named as in
        `ALLOWED_FUNCTIONS` (sqlglot's names next to the Postgres ones).
        """
        self._visible_marts = visible_marts
        self._allowed_functions = allowed_functions

    def check(self, sql: str) -> GuardResult:
        try:
            parsed = sqlglot.parse(sql, read=DIALECT)
        except (SqlglotError, RecursionError) as exc:
            first_line = str(exc).splitlines()[0].rstrip(".") if str(exc) else type(exc).__name__
            return _rejected(_error("parse", f"could not parse the query: {first_line}", "write one Postgres SELECT"))

        # A trailing `;` gives None, a trailing comment an empty Semicolon.
        statements = [stmt for stmt in parsed if stmt is not None and not isinstance(stmt, exp.Semicolon)]
        if len(statements) != 1:
            return _rejected(
                _error("statement_count", f"expected exactly one statement, got {len(statements)}", "send one SELECT")
            )

        statement = statements[0]
        if not isinstance(statement, _QUERY_NODES):
            hint = "remove the outer parentheses" if isinstance(statement, exp.Subquery) else "write one SELECT"
            return _rejected(
                _error("not_select", f"the statement must be a SELECT query, not {_statement_kind(statement)}", hint)
            )

        scopes = sum(1 for _ in statement.find_all(exp.Select))
        if scopes > MAX_QUERY_SCOPES:
            return _rejected(
                _error(
                    "complexity",
                    f"the query has {scopes} SELECTs, at most {MAX_QUERY_SCOPES} are allowed",
                    "split the question or use fewer CTEs and subqueries",
                )
            )

        errors: list[str] = []
        tables: list[str] = []
        clock_calls: list[str] = []
        for node in statement.walk():
            errors.extend(self._node_errors(node, tables))
            clock_calls.extend(name for kind, name in _CLOCK_CALLS if isinstance(node, kind))

        unique_errors = tuple(dict.fromkeys(errors))
        return GuardResult(
            ok=not unique_errors,
            errors=unique_errors,
            tables=tuple(dict.fromkeys(tables)),
            clock_calls=tuple(dict.fromkeys(clock_calls)),
        )

    def _node_errors(self, node: exp.Expr, tables: list[str]) -> list[str]:
        match node:
            case exp.Into():
                return [_error("select_into", "SELECT ... INTO is not allowed", "remove INTO")]
            case exp.Lock():
                return [_error("locking", "locking clause FOR UPDATE/SHARE is not allowed", "remove it")]
            case _ if isinstance(node, _WRITE_NODES):
                return [_error("write", f"{_statement_kind(node)} is not allowed", "only SELECT queries run")]
            case exp.With() if node.args.get("recursive"):
                # A recursive CTE can grow without bound inside the 5 s timeout.
                return [
                    _error(
                        "recursive",
                        "WITH RECURSIVE is not allowed",
                        "use marts.dim_date for a series of dates, or a non-recursive query",
                    )
                ]
            case exp.DataType() if node.this not in CAST_TYPES:
                return [
                    _error("cast", f"cast to type {node.sql(dialect=DIALECT)} is not allowed", CAST_HINT),
                ]
            case exp.Operator():
                # `a OPERATOR(pg_catalog.~) b` names any operator by its schema, past the rules for written ones.
                return [
                    _error(
                        "operator",
                        f"operator call `{_cut(node.sql(dialect=DIALECT))}` is not allowed",
                        DEFAULT_OPERATOR_HINT,
                    )
                ]
            case exp.Parameter() | exp.Placeholder():
                # Nothing binds a parameter, and sqlglot also reads Postgres's prefix `@` (absolute value) as one. Not
                # quoted: sqlglot renders `@x` as `$x` and `:x` as `%(x)s`.
                return [_error("parameter", PARAMETER_MESSAGE, PARAMETER_HINT)]
            case exp.Dot(expression=exp.Func() as func):
                return [
                    _error(
                        "function",
                        f"function {node.this.sql(dialect=DIALECT)}.{_written_name(func)} is not allowed",
                        "call functions without a schema",
                    )
                ]
            case exp.Func() if not isinstance(node, _OPERATOR_NODES):
                if _function_name(node) in self._allowed_functions:
                    return []

                name = _written_name(node)
                if _WRITTEN_NAME.fullmatch(name) is None:
                    return [_error("function", f"function or operator `{name}` is not allowed", _operator_hint(node))]

                return [_error("function", f"function {name} is not allowed", _function_hint(name))]
            case exp.Table():
                return self._table_errors(node, tables)
            case _:
                return []

    def _table_errors(self, table: exp.Table, tables: list[str]) -> list[str]:
        source = table.this
        if isinstance(source, exp.Func):
            # A table function is checked as a function; with a schema it is refused, as in a select list.
            qualifier = ".".join(part.name for part in (table.args.get("catalog"), table.args.get("db")) if part)
            if not qualifier:
                return []

            return [
                _error(
                    "function",
                    f"function {qualifier}.{_written_name(source)} is not allowed",
                    "call functions without a schema",
                )
            ]

        if not isinstance(source, exp.Identifier) or table.args.get("catalog") is not None:
            return [_error("table", f"table {table.sql(dialect=DIALECT)} is not allowed", "use marts.<name>")]

        name = _identifier_value(source)
        schema = table.args.get("db")
        if schema is None:
            if name in _ctes_in_scope(table):
                return []

            return [_error("table", f"table {name} is not schema-qualified", f"qualify it as {MARTS_SCHEMA}.{name}")]

        qualified = f"{_identifier_value(schema)}.{name}"
        if _identifier_value(schema) != MARTS_SCHEMA:
            return [_error("table", f"table {qualified} is outside {MARTS_SCHEMA}", _MARTS_HINT)]

        if name not in self._visible_marts:
            return [_error("table", f"table {qualified} is not a visible mart", _MARTS_HINT)]

        tables.append(qualified)
        return []


def _error(rule: str, message: str, hint: str) -> str:
    return f"{ERROR_CODE.format(rule=rule)}{message}; {hint}"


def _function_hint(name: str) -> str:
    if name in FUNCTION_HINTS:
        return FUNCTION_HINTS[name]
    if name.startswith(JSON_FUNCTION_PREFIXES):
        return JSON_HINT
    return DEFAULT_FUNCTION_HINT


def _operator_hint(node: exp.Expr) -> str:
    return next((hint for kind, hint in _OPERATOR_HINTS if isinstance(node, kind)), DEFAULT_OPERATOR_HINT)


def _cut(text: str) -> str:
    return f"{text[:ERROR_SNIPPET_LENGTH]}..." if len(text) > ERROR_SNIPPET_LENGTH else text


def _rejected(error: str) -> GuardResult:
    return GuardResult(ok=False, errors=(error,), tables=())


def _statement_kind(statement: exp.Expr) -> str:
    match statement:
        case exp.Command():
            return str(statement.this).upper()
        case exp.Alias():
            return _statement_kind(statement.this)  # `listen x` parses as the column `listen` aliased `x`
        case exp.Column():
            return statement.name.upper()  # a lone keyword (`checkpoint`) parses as a column
        case _:
            return _STATEMENT_KEYWORDS.get(statement.key, statement.key.upper())


def _function_name(func: exp.Func) -> str:
    return func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower()


def _written_name(func: exp.Func) -> str:
    """The name as the model wrote it in Postgres, not sqlglot's node name (`json_agg`, not `j_s_o_n_array_agg`).

    An operator form (`a ->> 'k'`, `a ~ 'x'`, `ARRAY[...]`) has no name, so the expression itself is returned, cut.
    """
    if isinstance(func, exp.Anonymous):
        return func.name.lower()

    rendered = func.sql(dialect=DIALECT)
    head = _CALL_HEAD.match(rendered)
    if head is not None and not _starts_with_operand(func, rendered):
        return head.group(1).lower()

    if _WRITTEN_NAME.fullmatch(rendered):
        return rendered.lower()  # a keyword function such as CURRENT_USER or LOCALTIMESTAMP

    if len(rendered) > ERROR_SNIPPET_LENGTH:
        return f"{rendered[:ERROR_SNIPPET_LENGTH]}..."

    return rendered


def _starts_with_operand(func: exp.Func, rendered: str) -> bool:
    """An infix operator on a call (`lower(x) ~ 'a'`) renders the operand first, so its first call is not its name."""
    return isinstance(func, exp.Binary) and rendered.startswith(func.this.sql(dialect=DIALECT))


def _identifier_value(identifier: exp.Identifier) -> str:
    """How Postgres resolves the name: unquoted names fold to lower case, quoted ones are kept verbatim."""
    return identifier.this if identifier.quoted else identifier.this.lower()


def _ctes_in_scope(table: exp.Expr) -> set[str]:
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
