from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
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
    from logfire.testing import CaptureLogfire
    from mcp import StdioServerParameters
    from mcp.types import CallToolResult

TOOL_NAMES = {
    *("get_context", "list_tables", "describe_table", "search_values"),
    *("get_metric_definitions", "get_examples", "validate_sql", "run_sql"),
}
COUNT_ORDERS = "select count(*) from marts.fct_orders"
# One call per tool, arguments in the order of the WarehouseTools method; compared with the in-process answer.
PARITY_CALLS: tuple[tuple[str, dict[str, object]], ...] = (
    ("get_context", {}),
    ("list_tables", {}),
    ("describe_table", {"table": "dim_customers"}),
    ("search_values", {"table": "dim_products", "column": "brand", "term": "haldin", "limit": 3}),
    ("get_metric_definitions", {}),
    ("get_examples", {"question": "net revenue by month", "k": 2}),
    ("validate_sql", {"sql": "select nope from marts.fct_orders"}),
    ("run_sql", {"sql": COUNT_ORDERS}),
)
VOLATILE_FIELDS = frozenset({"elapsed_ms"})
STDIO_TIMEOUT_SECONDS = 120
LEGACY_PROTOCOL_VERSION = "2025-06-18"  # the initialize handshake a pre-2026 client sends
HTTP_TIMEOUT_SECONDS = 30
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LEGACY_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "sherpa-tests", "version": "0"},
    },
}


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

    async def test_unexpected_error_is_recorded_server_side(
        self, caplog: pytest.LogCaptureFixture, capfire: CaptureLogfire
    ) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.run_sql.side_effect = RuntimeError("boom secret")

        with caplog.at_level(logging.ERROR):
            async with Client(build_server(tools)) as client:
                result = await client.call_tool("run_sql", {"sql": COUNT_ORDERS})

        assert result.is_error
        assert _text(result) == "Error executing tool run_sql"
        # The SDK logs the crash once, with the traceback, whose cause is the tool's own exception.
        (logged,) = [record for record in caplog.records if record.exc_info]
        assert logged.exc_info is not None
        assert isinstance(logged.exc_info[1], Exception)
        assert str(logged.exc_info[1].__cause__) == "boom secret"
        assert "Traceback" in caplog.text
        assert "RuntimeError: boom secret" in caplog.text
        events = [
            event
            for span in capfire.exporter.exported_spans_as_dict()
            for event in span.get("events", [])
            if event["name"] == "exception"
        ]
        assert [event["attributes"]["exception.message"] for event in events] == ["boom secret"]

    async def test_tool_schemas_bound_every_argument(self) -> None:
        async with Client(build_server(create_autospec(WarehouseTools, instance=True))) as client:
            listed = await client.list_tools()

        schemas = {tool.name: tool.input_schema["properties"] for tool in listed.tools}
        assert (schemas["search_values"]["limit"]["minimum"], schemas["search_values"]["limit"]["maximum"]) == (1, 50)
        assert (schemas["get_examples"]["k"]["minimum"], schemas["get_examples"]["k"]["maximum"]) == (1, 10)
        assert schemas["run_sql"]["sql"]["maxLength"] == 20_000
        assert schemas["validate_sql"]["sql"]["maxLength"] == 20_000
        assert schemas["search_values"]["term"]["maxLength"] == 200
        assert schemas["search_values"]["table"]["maxLength"] == 128
        assert schemas["search_values"]["column"]["maxLength"] == 128
        assert schemas["describe_table"]["table"]["maxLength"] == 128
        assert schemas["get_examples"]["question"]["maxLength"] == 2_000

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("run_sql", {"sql": "select 1 " + " " * 20_000}),
            ("search_values", {"table": "dim_products", "column": "brand", "term": "x" * 201}),
            ("search_values", {"table": "dim_products", "column": "brand", "term": "x", "limit": 51}),
            ("get_examples", {"question": "q", "k": 0}),
            ("get_examples", {"question": "q" * 2_001}),
        ],
    )
    async def test_out_of_bounds_arguments_never_reach_the_tools(self, tool: str, arguments: dict[str, object]) -> None:
        tools = create_autospec(WarehouseTools, instance=True)

        async with Client(build_server(tools)) as client:
            result = await client.call_tool(tool, arguments)

        assert result.is_error
        getattr(tools, tool).assert_not_called()

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

    async def test_every_tool_answers_as_in_process(self, mcp_client: Client, tools: WarehouseTools) -> None:
        assert {name for name, _ in PARITY_CALLS} == TOOL_NAMES

        for name, arguments in PARITY_CALLS:
            expected = getattr(tools, name)(*arguments.values()).model_dump(mode="json")

            result = await mcp_client.call_tool(name, arguments)

            assert not result.is_error, (name, _text(result))
            assert _without_volatile(result.structured_content) == _without_volatile(expected), name

    async def test_tool_error_is_an_mcp_error(self, mcp_client: Client) -> None:
        result = await mcp_client.call_tool("run_sql", {"sql": "delete from marts.fct_orders"})

        assert result.is_error
        assert "must be a SELECT query, not DELETE" in _text(result)


@pytest.mark.warehouse
def test_stdio_stdout_carries_only_protocol(stdio_server: StdioServerParameters) -> None:
    requests = (
        INITIALIZE,
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


@pytest.mark.warehouse
@pytest.mark.parametrize(
    ("host", "status"),
    [("127.0.0.1", 200), ("localhost", 200), ("mcp", 200), ("evil.example", 421)],
)
def test_http_on_all_interfaces_checks_the_host_header(mcp_all_interfaces_port: int, host: str, status: int) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{mcp_all_interfaces_port}/mcp",
        data=json.dumps(INITIALIZE).encode(),
        headers={
            "Host": f"{host}:{mcp_all_interfaces_port}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            answered = response.status
    except urllib.error.HTTPError as error:
        answered = error.code

    assert answered == status


def _without_volatile(value: object) -> object:
    """`value` with every timing field dropped, at any depth."""
    match value:
        case dict():
            return {key: _without_volatile(item) for key, item in value.items() if key not in VOLATILE_FIELDS}
        case list():
            return [_without_volatile(item) for item in value]
        case _:
            return value


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))
