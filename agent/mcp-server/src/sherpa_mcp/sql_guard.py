"""Static check that a model-written query is one read-only SELECT over the visible marts (agent spec §4.2)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from sherpa_mcp.catalog import MARTS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping

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
# lpad/rpad build a string of the length they are given, so only a literal length up to the cell cap the server
# returns (database.MAX_CELL_CHARS) is allowed: a computed or huge length could fill the server's memory.
MAX_PAD_LENGTH: Final = 10_000
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
_MARTS_HINT: Final = "query only the marts that list_tables returns"
_OPERATOR_HINTS: Final[tuple[tuple[type[exp.Expr], str], ...]] = (
    (exp.Array, "use IN (...) instead of = ANY(ARRAY[...])"),
    (exp.RegexpLike, "use LIKE or ILIKE instead of a regular expression"),
    (exp.RegexpILike, "use LIKE or ILIKE instead of a regular expression"),
)
# Calls that read the real clock; allowed, but the warehouse's "today" is its as-of date (agent spec §4.1).
_CLOCK_NODES: Final = (exp.CurrentTimestamp, exp.CurrentDate, exp.CurrentTime, exp.Localtimestamp, exp.Localtime)
_CALL_HEAD: Final = re.compile(r"([A-Za-z_]\w*)\s*\(")
_WRITTEN_NAME: Final = re.compile(r"[A-Za-z_]\w*")
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
    clock_calls: tuple[str, ...] = ()  # now(), current_date, ... as written; not errors


class SqlGuard:
    """Never rewrites the SQL and never raises on bad SQL: every problem is an error in the result."""

    def __init__(
        self, mart_columns: Mapping[str, frozenset[str]], allowed_functions: frozenset[str] = ALLOWED_FUNCTIONS
    ) -> None:
        """`mart_columns`: every visible mart and the names of its columns, as Postgres stores them.

        `allowed_functions` defaults to the agent's list; another caller (the UI) may pass its own, named as in
        `ALLOWED_FUNCTIONS` (sqlglot's names next to the Postgres ones).
        """
        self._mart_columns = mart_columns
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

        errors: list[str] = []
        tables: list[str] = []
        clock_calls: list[str] = []
        for node in statement.walk():
            errors.extend(self._node_errors(node, tables))
            if isinstance(node, _CLOCK_NODES):
                clock_calls.append(_written_name(node))
        errors.extend(self._qualified_column_errors(statement))

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
            case exp.Pad() if not _is_small_literal(node.expression):
                return [
                    _error(
                        "pad",
                        f"{_written_name(node)} needs a literal length of at most {MAX_PAD_LENGTH:,}",
                        f"write the length as a number, e.g. {_written_name(node)}(x::text, 2, '0')",
                    )
                ]
            case exp.DataType() if not _is_built_in_type(node):
                return [
                    _error(
                        "cast",
                        f"cast to type {node.sql(dialect=DIALECT)} is not allowed",
                        "cast to a built-in type such as text, numeric, date or timestamp",
                    )
                ]
            case exp.Dot(expression=exp.Func() as func):
                return [
                    _error(
                        "function",
                        f"function {node.this.sql(dialect=DIALECT)}.{_written_name(func)} is not allowed",
                        "call functions without a schema",
                    )
                ]
            case exp.Dot():
                # `(expr).name` or a five-part name: Postgres may read the last part as a function of the rest.
                return [
                    _error(
                        "field",
                        f"field selection `{_cut(node.sql(dialect=DIALECT))}` is not allowed",
                        "select named columns",
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
            case exp.Column(this=exp.Star()) if not _is_projection_item(node):
                # `c.*` expands to named (maskable) columns only as a select-list item; anywhere else it is one record.
                return [
                    _error(
                        "whole_row",
                        f"whole-row reference `{node.sql(dialect=DIALECT)}` inside an expression is not allowed",
                        "select named columns",
                    )
                ]
            case exp.Column(table="") if _is_whole_row(node):
                return [
                    _error("whole_row", f"whole-row reference `{node.name}` is not allowed", "select named columns")
                ]
            case _:
                return []

    def _table_errors(self, table: exp.Table, tables: list[str]) -> list[str]:
        source = table.this
        if isinstance(source, exp.Func):
            return []  # a table function: checked as a function

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

        if name not in self._mart_columns:
            return [_error("table", f"table {qualified} is not a visible mart", _MARTS_HINT)]

        tables.append(qualified)
        return []

    def _qualified_column_errors(self, statement: exp.Expr) -> list[str]:
        """Every qualified column must be a real column of the source its qualifier names.

        Postgres reads `t.f` as the call `f(t)` when `f` is not a column of `t`, so `c.row_to_json` or `c.to_jsonb`
        would return the whole row past the function allow-list and the name-based masking. A qualifier that does not
        resolve to a mart or a derived table whose columns are known fails closed. Identifiers are folded as Postgres
        folds them (unquoted to lower case), on a copy: the query itself is never rewritten.
        """
        tree = normalize_identifiers(statement.copy(), dialect=DIALECT)
        try:
            scopes = {id(scope.expression): scope for scope in traverse_scope(tree)}
        except (SqlglotError, RecursionError):
            return [_error("column", "could not resolve the column references of the query", "simplify it")]

        errors: list[str] = []
        for column in tree.find_all(exp.Column):
            if not column.table or isinstance(column.this, exp.Star):
                continue  # unqualified (never a function call) or `t.*` (checked as a whole-row reference)

            scope = _enclosing_scope(column, scopes)
            source = None if scope is None or column.args.get("catalog") else _find_source(scope, column)
            outputs = None if source is None else self._outputs(source, scopes, frozenset())
            if outputs is None or column.name not in outputs:
                errors.append(_not_a_column(column, source))
        return errors

    def _outputs(
        self, source: exp.Expr | Scope, scopes: dict[int, Scope], seen: frozenset[int]
    ) -> frozenset[str] | None:
        """The column names a FROM source exposes, or None when they cannot be known (the caller fails closed)."""
        if isinstance(source, exp.Table):
            renamed = _alias_columns(source)
            if renamed is not None:
                return renamed  # Postgres renames by position; only the new names are certain

            if source.db != MARTS_SCHEMA or not isinstance(source.this, exp.Identifier) or source.args.get("catalog"):
                return None
            return self._mart_columns.get(source.name)

        if not isinstance(source, Scope) or id(source) in seen:
            return None  # not a query, or a recursive reference

        seen = seen | {id(source)}
        expression = source.expression
        renamed = _alias_columns(expression)
        if renamed is not None:
            return renamed

        match expression:
            case exp.SetOperation():
                # A set operation's columns are named by its first query.
                left = expression.left.unnest()
                return self._outputs(scopes[id(left)], scopes, seen) if id(left) in scopes else None
            case exp.Lateral(this=exp.Subquery(this=inner)) if id(inner) in scopes:
                return self._outputs(scopes[id(inner)], scopes, seen)
            case exp.Select():
                return self._select_outputs(source, expression, scopes, seen)
            case _:
                return None

    def _select_outputs(
        self, scope: Scope, select: exp.Select, scopes: dict[int, Scope], seen: frozenset[int]
    ) -> frozenset[str] | None:
        names: set[str] = set()
        for projection in select.expressions:
            expanded: list[frozenset[str] | None]
            match projection:
                case exp.Star():
                    expanded = [self._outputs(item, scopes, seen) for _, item in scope.selected_sources.values()]
                case exp.Column(this=exp.Star()):
                    item = _find_source(scope, projection)
                    expanded = [None if item is None else self._outputs(item, scopes, seen)]
                case _:
                    expanded = [frozenset({projection.output_name})]
            for outputs in expanded:
                if outputs is None:
                    return None
                names.update(outputs)
        return frozenset(names)


def _enclosing_scope(column: exp.Column, scopes: dict[int, Scope]) -> Scope | None:
    node = column.parent
    while node is not None and id(node) not in scopes:
        node = node.parent
    return None if node is None else scopes[id(node)]


def _find_source(scope: Scope, column: exp.Column) -> exp.Expr | Scope | None:
    """The FROM item that `column`'s qualifier names, searched outwards from the innermost query as Postgres does."""
    current: Scope | None = scope
    while current is not None:
        selected = current.selected_sources.get(column.table)
        if selected is not None:
            source: exp.Expr | Scope = selected[1]
            schema = column.args.get("db")
            if schema is None:
                return source
            # `schema.table.column` only names a table that has no alias.
            if isinstance(source, exp.Table) and not source.alias and source.db == schema.name:
                return source
            return None

        current = current.parent
    return None


def _alias_columns(node: exp.Expr) -> frozenset[str] | None:
    """The column names an alias list gives a source (`s(a, b)`, `with t(a, b) as`), if it has one."""
    alias = node.args.get("alias")
    if alias is None and isinstance(node.parent, exp.Subquery | exp.CTE):
        alias = node.parent.args.get("alias")
    if not isinstance(alias, exp.TableAlias) or not alias.columns:
        return None
    return frozenset(column.name for column in alias.columns)


def _not_a_column(column: exp.Column, source: exp.Expr | Scope | None) -> str:
    match source:
        case exp.Table():
            target = f"{source.db}.{source.name}" if source.db else source.name
        case None:
            target = "a table in scope"
        case _:
            target = column.table
    return _error(
        "column",
        f"column {column.sql(dialect=DIALECT)} is not a column of {target}",
        "use a column listed by describe_table",
    )


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


def _is_small_literal(length: exp.Expr | None) -> bool:
    return (
        isinstance(length, exp.Literal)
        and not length.is_string
        and length.is_int
        and int(length.this) <= MAX_PAD_LENGTH
    )


def _cut(text: str) -> str:
    return f"{text[:ERROR_SNIPPET_LENGTH]}..." if len(text) > ERROR_SNIPPET_LENGTH else text


def _rejected(error: str) -> GuardResult:
    return GuardResult(ok=False, errors=(error,), tables=())


def _statement_kind(statement: exp.Expr) -> str:
    if isinstance(statement, exp.Command):
        return str(statement.this).upper()

    return statement.key.upper()


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
    if head is not None:
        return head.group(1).lower()

    if _WRITTEN_NAME.fullmatch(rendered):
        return rendered.lower()  # a keyword function such as CURRENT_USER or LOCALTIMESTAMP

    if len(rendered) > ERROR_SNIPPET_LENGTH:
        return f"{rendered[:ERROR_SNIPPET_LENGTH]}..."

    return rendered


def _is_built_in_type(data_type: exp.DataType) -> bool:
    """sqlglot knows the standard types; reg* types (catalog lookups), domains and qualified types it does not."""
    return isinstance(data_type.this, exp.DataType.Type) and data_type.this != exp.DataType.Type.USERDEFINED


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


def _is_projection_item(column: exp.Column) -> bool:
    return isinstance(column.parent, exp.Select) and column.arg_key == "expressions"


def _is_whole_row(column: exp.Column) -> bool:
    """An unqualified name that is a table alias, table name or CTE name in scope reads the whole record as one value.

    Postgres resolves a real column first, so this also rejects a column that shares such a name; that is rare and the
    model can qualify it (`alias.column`).
    """
    # Fails closed: compared case-insensitively on both sides, so `"C"`, `C` and `c` all count as the same name.
    name = _identifier_value(column.this).lower()
    if name in {cte.lower() for cte in _ctes_in_scope(column)}:
        return True

    scope = column.parent
    while scope is not None:
        if isinstance(scope, exp.Select):
            sources = [scope.args.get("from_") or scope.args.get("from"), *scope.args.get("joins", [])]
            for source in sources:
                relation = source.this if source is not None else None
                if relation is not None and relation.alias_or_name.lower() == name:
                    return True
        scope = scope.parent
    return False
