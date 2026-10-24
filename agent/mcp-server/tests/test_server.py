from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
from mcp import Client
from mcp.client.stdio import get_default_environment
from mcp.types import TextContent
from sherpa_mcp.database import WarehouseToolError
from sherpa_mcp.server import build_server
from sherpa_mcp.tools import WarehouseTools

if TYPE_CHECKING:
    from mcp import StdioServerParameters
    from mcp.types import CallToolResult

TOOL_NAMES = {
    *("get_context", "list_tables", "describe_table", "search_values"),
    *("get_metric_definitions", "get_examples", "validate_sql", "run_sql"),
}
COUNT_ORDERS = "select count(*) from marts.fct_orders"
STDIO_TIMEOUT_SECONDS = 120
LEGACY_PROTOCOL_VERSION = "2025-06-18"  # the initialize handshake a pre-2026 client sends


@pytest.mark.anyio
class TestBuildServer:
    async def test_warehouse_tool_error_reaches_the_model(self) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.describe_table.side_effect = WarehouseToolError("unknown table 'nope'; valid tables: dim_date")

        async with Client(build_server(tools)) as client:
            result = await client.call_tool("describe_table", {"table": "nope"})

        assert result.is_error
        assert "unknown table 'nope'; valid tables: dim_date" in _text(result)

    async def test_unexpected_error_hides_its_internals(self) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.list_tables.side_effect = RuntimeError("connection failed: password=hunter2 host=10.0.0.1")

        async with Client(build_server(tools)) as client:
            result = await client.call_tool("list_tables", {})

        assert result.is_error
        assert "hunter2" not in _text(result)
        assert "10.0.0.1" not in _text(result)

    async def test_arguments_reach_the_tools_by_spec_name(self) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.validate_sql.side_effect = WarehouseToolError("seen")

        async with Client(build_server(tools)) as client:
            await client.call_tool("validate_sql", {"sql": COUNT_ORDERS})
            await client.call_tool("search_values", {"table": "dim_products", "column": "brand", "term": "acme"})
            await client.call_tool("get_examples", {"question": "revenue by month"})

        tools.validate_sql.assert_called_once_with(COUNT_ORDERS)
        tools.search_values.assert_called_once_with("dim_products", "brand", "acme", 10)
        tools.get_examples.assert_called_once_with("revenue by month", 3)


@pytest.mark.anyio
@pytest.mark.warehouse
class TestServer:
    async def test_lists_the_eight_tools(self, mcp_client: Client) -> None:
        listed = await mcp_client.list_tools()

        assert {tool.name for tool in listed.tools} == TOOL_NAMES
        assert all(tool.description for tool in listed.tools)

    async def test_same_answers_over_both_transports(self, mcp_client: Client, tools: WarehouseTools) -> None:
        expected_run = tools.run_sql(COUNT_ORDERS).model_dump(mode="json", exclude={"elapsed_ms"})

        tables = await mcp_client.call_tool("list_tables", {})
        context = await mcp_client.call_tool("get_context", {})
        run = await mcp_client.call_tool("run_sql", {"sql": COUNT_ORDERS})

        assert tables.structured_content == tools.list_tables().model_dump(mode="json")
        assert context.structured_content == tools.get_context().model_dump(mode="json")
        assert run.structured_content is not None
        assert {key: value for key, value in run.structured_content.items() if key != "elapsed_ms"} == expected_run

    async def test_tool_error_is_an_mcp_error(self, mcp_client: Client) -> None:
        result = await mcp_client.call_tool("run_sql", {"sql": "delete from marts.fct_orders"})

        assert result.is_error
        assert "must be a SELECT query, not DELETE" in _text(result)


@pytest.mark.warehouse
def test_stdio_stdout_carries_only_protocol(stdio_server: StdioServerParameters) -> None:
    requests = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "stdout-test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    stdin = "".join(json.dumps(request) + "\n" for request in requests)

    # Ending stdin is the stdio shutdown signal; the server answers what it read, then exits.
    completed = subprocess.run(
        [stdio_server.command, *stdio_server.args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=stdio_server.cwd,
        env=get_default_environment() | (stdio_server.env or {}),
        timeout=STDIO_TIMEOUT_SECONDS,
        check=False,
    )

    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    assert messages, completed.stderr
    assert all(message["jsonrpc"] == "2.0" for message in messages)
    responses = {message["id"]: message for message in messages if "id" in message}
    assert {tool["name"] for tool in responses[2]["result"]["tools"]} == TOOL_NAMES


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))
