"""The eight tools the model gets (agent spec §4.1). Each method takes and returns frozen Pydantic models."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import TYPE_CHECKING, Final

import logfire
from psycopg import sql
from pydantic import BaseModel, ConfigDict

from sherpa_mcp.catalog import MARTS_SCHEMA, CatalogUnavailable, MartCatalog, MartColumn, MetricDefinition
from sherpa_mcp.database import WarehouseToolError
from sherpa_mcp.examples import MAX_K, MIN_K, Example
from sherpa_mcp.masking import PiiMasker
from sherpa_mcp.sql_guard import SqlGuard

if TYPE_CHECKING:
    from sherpa_mcp.catalog import Mart, MartCatalogLoader
    from sherpa_mcp.database import AgentDatabase
    from sherpa_mcp.examples import ExampleIndex
    from sherpa_mcp.sql_guard import GuardResult

SAMPLE_ROWS: Final = 3
MIN_LIMIT: Final = 1
MAX_LIMIT: Final = 50
EXECUTION_ID_LENGTH: Final = 16
TEXT_TYPE: Final = "text"
GUARD_SPAN: Final = "sherpa.guard"
# The `[guard:<rule>]` code every sql_guard error starts with; what follows it may quote the query.
GUARD_RULE: Final = re.compile(r"\[guard:(\w+)\]")
UNKNOWN_RULE: Final = "unknown"
AS_OF_QUERY: Final = "select max(order_date) from marts.fct_orders"
CLOCK_WARNING: Final = (
    "{calls} reads the real clock, but the warehouse is as of {as_of}; "
    "derive periods from (select max(order_date) from marts.fct_orders)"
)
# Fixed: what the warehouse cannot answer, so the model says so instead of guessing. The last three leave this
# tuple when data-platform-3 and -4 land.
DOES_NOT_COVER: Final = (
    "inventory and stock levels",
    "product reviews",
    "currencies other than EUR",
    "web sessions (not yet loaded)",
    "marketing campaigns and ad spend (not yet loaded)",
    "leads (not yet loaded)",
)


class WarehouseContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of_date: dt.date
    first_order_date: dt.date
    domains: tuple[str, ...]
    covers: tuple[str, ...]
    does_not_cover: tuple[str, ...]


class TableSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    domain: str
    grain: str
    primary_key: tuple[str, ...]


class TableList(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: tuple[TableSummary, ...]


class ColumnDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    description: str
    is_pii: bool
    accepted_values: tuple[str, ...] | None
    synonyms: tuple[str, ...]


class SampleRows(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


class TableDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    grain: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnDescription, ...]
    sample_rows: SampleRows


class ValueMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    similarity: float
    row_count: int


class ValueMatches(BaseModel):
    model_config = ConfigDict(frozen=True)

    matches: tuple[ValueMatch, ...]


class MetricList(BaseModel):
    model_config = ConfigDict(frozen=True)

    metrics: tuple[MetricDefinition, ...]


class ExampleList(BaseModel):
    model_config = ConfigDict(frozen=True)

    examples: tuple[Example, ...]


class Validation(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    errors: tuple[str, ...]
    tables: tuple[str, ...]
    warnings: tuple[str, ...] = ()  # non-blocking: the query may run, but likely answers the wrong question


class SqlResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    returned_rows: int
    truncated: bool
    elapsed_ms: int


class WarehouseTools:
    def __init__(self, loader: MartCatalogLoader, database: AgentDatabase, examples: ExampleIndex) -> None:
        self._loader = loader
        self._database = database
        self._examples = examples

    def get_context(self) -> WarehouseContext:
        catalog = self._catalog()
        latest = self._database.scalar(AS_OF_QUERY)
        earliest = self._database.scalar("select min(order_date) from marts.fct_orders")
        if not isinstance(latest, dt.date) or not isinstance(earliest, dt.date):  # null: max/min over no rows
            raise WarehouseToolError("fct_orders has no rows; run make build-prod")
        covers = tuple(
            f"{domain}: {', '.join(mart.name for mart in catalog.marts if mart.domain == domain)}"
            for domain in catalog.domains()
        )
        return WarehouseContext(
            as_of_date=latest,
            first_order_date=earliest,
            domains=catalog.domains(),
            covers=covers,
            does_not_cover=DOES_NOT_COVER,
        )

    def list_tables(self) -> TableList:
        return TableList(
            tables=tuple(
                TableSummary(
                    name=mart.name,
                    description=mart.description,
                    domain=mart.domain,
                    grain=mart.grain,
                    primary_key=mart.primary_key,
                )
                for mart in self._catalog().marts
            )
        )

    def describe_table(self, table: str) -> TableDescription:
        catalog = self._catalog()
        mart = _resolve_mart(catalog, table)
        order_by = sql.SQL(", ").join(sql.Identifier(column) for column in mart.primary_key)
        query = sql.SQL("select * from {}.{} order by {} limit {}").format(
            sql.Identifier(MARTS_SCHEMA), sql.Identifier(mart.name), order_by, sql.Literal(SAMPLE_ROWS)
        )
        result = self._database.fetch_for_model(query, max_rows=SAMPLE_ROWS)
        masked = PiiMasker(catalog.pii_columns()).mask(result.columns, result.rows)
        sample = SampleRows(columns=result.columns, rows=tuple(tuple(row) for row in masked))
        return TableDescription(
            name=mart.name,
            description=mart.description,
            grain=mart.grain,
            primary_key=mart.primary_key,
            columns=tuple(
                ColumnDescription(
                    name=column.name,
                    data_type=column.data_type,
                    description=column.description,
                    is_pii=column.is_pii,
                    accepted_values=column.accepted_values,
                    synonyms=column.synonyms,
                )
                for column in mart.columns
            ),
            sample_rows=sample,
        )

    def search_values(self, table: str, column: str, term: str, limit: int = 10) -> ValueMatches:
        if not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise WarehouseToolError(f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}, got {limit}")
        mart = _resolve_mart(self._catalog(), table)
        found = _resolve_column(mart, column)
        if found.is_pii:
            raise WarehouseToolError(f"{mart.name}.{found.name} holds PII and cannot be searched")
        if found.data_type != TEXT_TYPE:
            raise WarehouseToolError(
                f"{mart.name}.{found.name} is {found.data_type}, not text; only text columns can be searched"
            )
        name = sql.Identifier(found.name)
        # `%%` is the pg_trgm operator; the term only ever travels as a bound parameter.
        query = sql.SQL(
            "select {col} as value, similarity({col}, %s) as similarity, count(*) as row_count "
            "from {schema}.{mart} where {col} %% %s group by 1 order by 2 desc, 1 limit %s"
        ).format(col=name, schema=sql.Identifier(MARTS_SCHEMA), mart=sql.Identifier(mart.name))
        result = self._database.fetch(query, (term, term, limit), max_rows=limit, set_similarity_threshold=True)
        return ValueMatches(
            matches=tuple(
                # psycopg's own types (text, real, bigint); the model checks them.
                ValueMatch.model_validate(dict(zip(result.columns, row, strict=True)))
                for row in result.rows
            )
        )

    def get_metric_definitions(self) -> MetricList:
        return MetricList(metrics=self._catalog().metrics)

    def get_examples(self, question: str, k: int = 3) -> ExampleList:
        if not MIN_K <= k <= MAX_K:
            raise WarehouseToolError(f"k must be between {MIN_K} and {MAX_K}, got {k}")
        return ExampleList(examples=tuple(self._examples.top(question, k)))

    def validate_sql(self, sql_text: str) -> Validation:
        executed = _as_executed(sql_text)
        guard = self._guard("validate_sql", executed, self._catalog())
        if not guard.ok:
            return Validation(ok=False, errors=guard.errors, tables=guard.tables)
        warnings = self._clock_warnings(guard)
        try:
            self._database.explain(executed)
        except WarehouseToolError as error:
            return Validation(ok=False, errors=(str(error),), tables=guard.tables, warnings=warnings)
        return Validation(ok=True, errors=(), tables=guard.tables, warnings=warnings)

    def run_sql(self, sql_text: str) -> SqlResult:
        catalog = self._catalog()
        executed = _as_executed(sql_text)
        execution_id = _execution_id(executed)
        guard = self._guard("run_sql", executed, catalog, execution_id=execution_id)
        if not guard.ok:
            raise WarehouseToolError("; ".join(guard.errors))
        result = self._database.fetch_for_model(executed)
        masked = PiiMasker(catalog.pii_columns()).mask(result.columns, result.rows)
        return SqlResult(
            execution_id=execution_id,
            columns=result.columns,
            rows=tuple(tuple(row) for row in masked),
            returned_rows=result.returned_rows,
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
        )

    def _catalog(self) -> MartCatalog:
        try:
            return self._loader.load()
        except CatalogUnavailable as error:
            raise WarehouseToolError(str(error)) from error

    def _clock_warnings(self, guard: GuardResult) -> tuple[str, ...]:
        """A query that reads today's date answers for the wrong period: the data stops at the as-of date."""
        if not guard.clock_calls:
            return ()
        as_of = self._database.scalar(AS_OF_QUERY)
        return (CLOCK_WARNING.format(calls=", ".join(guard.clock_calls), as_of=as_of),)

    @staticmethod
    def _guard(tool: str, executed: str, catalog: MartCatalog, execution_id: str | None = None) -> GuardResult:
        """Run the guard inside a `sherpa.guard` span (agent spec §6). Never the SQL or the error texts on the trace:
        both can carry literals, and literals can be PII."""
        with logfire.span(GUARD_SPAN, tool=tool) as span:
            # Built per call from the catalog of this call, so its columns always match the marts it checks against.
            guard = SqlGuard(catalog.mart_columns()).check(executed)
            span.set_attributes({"ok": guard.ok, "error_count": len(guard.errors), "tables": guard.tables})
            if execution_id is not None:
                span.set_attribute("execution_id", execution_id)
            if not guard.ok:
                logfire.info(
                    "guard rejected", tool=tool, error_count=len(guard.errors), rule=_rule_class(guard.errors[0])
                )

        return guard


def _execution_id(executed: str) -> str:
    return hashlib.sha256(executed.encode()).hexdigest()[:EXECUTION_ID_LENGTH]


def _rule_class(error: str) -> str:
    """The guard rule an error message comes from, by its `[guard:<rule>]` code; the rest may quote the query."""
    code = GUARD_RULE.match(error)
    return UNKNOWN_RULE if code is None else code.group(1)


def _as_executed(sql_text: str) -> str:
    stripped = sql_text.rstrip()
    while stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    return stripped


def _resolve_mart(catalog: MartCatalog, table: str) -> Mart:
    mart = catalog.mart(table)
    if mart is None:
        valid = ", ".join(mart.name for mart in catalog.marts)
        raise WarehouseToolError(f"unknown table {table!r}; valid tables: {valid}")
    return mart


def _resolve_column(mart: Mart, column: str) -> MartColumn:
    found = mart.column(column)
    if found is None:
        valid = ", ".join(item.name for item in mart.columns)
        raise WarehouseToolError(f"unknown column {column!r} in {mart.name}; valid columns: {valid}")
    return found
