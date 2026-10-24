"""The `sherpa-mcp` server: the eight warehouse tools over stdio or Streamable HTTP (agent spec §3, §4.1, §6).

stdout carries the stdio JSON-RPC stream, so nothing here may write to it: logging goes to stderr and Logfire runs
without its console exporter.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final

import logfire
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.database import AgentDatabase, WarehouseToolError
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.settings import McpSettings
from sherpa_mcp.tools import (
    ExampleList,
    MetricList,
    SqlResult,
    TableDescription,
    TableList,
    Validation,
    ValueMatches,
    WarehouseContext,
    WarehouseTools,
)

SERVICE_NAME: Final = "sherpa-mcp"
MCP_PATH: Final = "/mcp"  # agent spec §10: MCP_HTTP_URL = http://127.0.0.1:8765/mcp
EXAMPLES_PATH: Final = Path(__file__).with_name("examples.yaml")
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)

TableName = Annotated[str, Field(description="A mart name from list_tables, e.g. fct_orders (case-insensitive).")]
SqlText = Annotated[
    str, Field(description="One Postgres SELECT (or WITH ... SELECT); every table written as marts.<mart>.")
]


def build_server(tools: WarehouseTools) -> MCPServer:
    """Register the eight tools; each delegates to `tools` and turns a `WarehouseToolError` into a tool error."""
    server: MCPServer = MCPServer(SERVICE_NAME)

    @server.tool(
        description=(
            "The warehouse's as-of date (the latest order date; use it instead of today for 'last month', "
            "'this year' and other relative periods), the first order date, the domains, and what the warehouse "
            "covers and does not cover. Call it first; if a question needs something listed under does_not_cover, "
            "say the warehouse cannot answer it instead of guessing."
        )
    )
    def get_context() -> WarehouseContext:
        with _tool_errors():
            return tools.get_context()

    @server.tool(
        description=(
            "Every mart you may query, with its description, domain, grain (what one row is) and primary key. "
            "Use it to pick the tables for a question."
        )
    )
    def list_tables() -> TableList:
        with _tool_errors():
            return tools.list_tables()

    @server.tool(
        description=(
            "One mart in detail: its grain and primary key, and per column the data type, description, whether it "
            "is PII, the accepted values (if the column has a fixed set) and synonyms; plus 3 sample rows ordered "
            "by the primary key, with PII masked as '***'. Call it before writing SQL against a mart."
        )
    )
    def describe_table(table: TableName) -> TableDescription:
        with _tool_errors():
            return tools.describe_table(table)

    @server.tool(
        description=(
            "Find how a value is actually stored in a text column, by trigram similarity: up to `limit` distinct "
            "stored values similar to `term`, best first, each with its similarity and row count. Use it before "
            "filtering on a name the user typed (a brand, a city, a category) so the WHERE clause matches the "
            "stored spelling. Only text columns can be searched, and PII columns are refused."
        )
    )
    def search_values(
        table: TableName,
        column: Annotated[str, Field(description="A text column of that mart, from describe_table.")],
        term: Annotated[str, Field(description="The value to look for, as the user wrote it.")],
        limit: Annotated[int, Field(description="How many values to return, 1 to 50.")] = 10,  # agent spec §4.1
    ) -> ValueMatches:
        with _tool_errors():
            return tools.search_values(table, column, term, limit)

    @server.tool(
        description=(
            "The warehouse's official metric definitions (gross revenue, net revenue, AOV, refund rate, ...), "
            "as name and definition text. Follow them exactly whenever a question names a metric."
        )
    )
    def get_metric_definitions() -> MetricList:
        with _tool_errors():
            return tools.get_metric_definitions()

    @server.tool(
        description=(
            "The k worked examples (question, SQL, notes) most similar to a question, showing the warehouse's "
            "query patterns: aggregation by period, top-N, ratio metrics, funnel flags. Adapt them; do not copy "
            "them blindly."
        )
    )
    def get_examples(
        question: Annotated[str, Field(description="The user's question in natural language.")],
        k: Annotated[int, Field(description="How many examples to return, 1 to 10.")] = 3,  # agent spec §4.1
    ) -> ExampleList:
        with _tool_errors():
            return tools.get_examples(question, k)

    @server.tool(
        description=(
            "Check a query without running it: the safety rules (one read-only SELECT, every table written as "
            "marts.<mart>, allowed functions only), then a Postgres EXPLAIN. Returns ok, the errors to fix and "
            "the tables the query reads."
        )
    )
    def validate_sql(sql: SqlText) -> Validation:
        with _tool_errors():
            return tools.validate_sql(sql)

    @server.tool(
        description=(
            "Run one read-only SELECT exactly as written (a trailing ';' is dropped) and return its columns and "
            "at most 100 rows. truncated is true when there were more rows: aggregate or filter instead of "
            "relying on a partial result; returned_rows is never a total. PII columns come back as '***'. "
            "Queries are cancelled after 5 s. execution_id identifies the executed SQL."
        )
    )
    def run_sql(sql: SqlText) -> SqlResult:
        with _tool_errors():
            return tools.run_sql(sql)

    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=LOG_FORMAT)
    logfire.configure(service_name=SERVICE_NAME, console=False, send_to_logfire="if-token-present")
    # Named: without an argument Logfire instruments every psycopg it finds, and dbt brings psycopg2 to the same venv.
    # Before the pool opens its first connection.
    logfire.instrument_psycopg("psycopg")
    settings = McpSettings()  # type: ignore[call-arg]  # fields come from the environment / .env
    database = AgentDatabase(settings)
    tools = WarehouseTools(MartCatalogLoader(settings.dbt_target_path), database, ExampleIndex.from_yaml(EXAMPLES_PATH))
    server = build_server(tools)
    logger.info("serving the warehouse tools over %s", settings.transport)
    try:
        match settings.transport:
            case "stdio":
                server.run("stdio")
            case "http":
                server.run(
                    "streamable-http", host=settings.mcp_host, port=settings.mcp_port, streamable_http_path=MCP_PATH
                )
    finally:
        database.close()


@contextmanager
def _tool_errors() -> Iterator[None]:
    """A `WarehouseToolError` is meant for the model; anything else stays a crash the SDK reports without detail."""
    try:
        yield
    except WarehouseToolError as error:
        raise ToolError(str(error)) from error


if __name__ == "__main__":
    main()
