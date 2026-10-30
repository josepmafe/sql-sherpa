from __future__ import annotations

import json
import logging
import subprocess
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from mcp import Client
from mcp.client.stdio import get_default_environment
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp.types import TextContent
from sherpa_mcp.database import WarehouseToolError
from sherpa_mcp.server import STARTUP_WAIT_SECONDS, _transport_security, build_server, configure_logging
from sherpa_mcp.tools import WarehouseTools
from starlette.requests import Request

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
TOOLS_LIST_ID = 2
STARTUP_FAILURE_TIMEOUT_SECONDS = 60  # the 15 s startup wait plus process start; it took 25 s once
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

    async def test_each_tool_call_is_a_span_without_arguments_or_error_text(self, capfire: CaptureLogfire) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.run_sql.side_effect = WarehouseToolError("[guard:write] must be a SELECT query: 'leak@x.com'")

        async with Client(build_server(tools)) as client:
            await client.call_tool("run_sql", {"sql": "delete from marts.dim_customers where email = 'leak@x.com'"})

        spans = capfire.exporter.exported_spans_as_dict()
        (call,) = [span for span in spans if span["name"] == "tools/call run_sql"]
        assert call["attributes"]["gen_ai.tool.name"] == "run_sql"
        assert call["attributes"]["error.type"] == "tool_error"
        assert "leak@x.com" not in json.dumps(spans, default=str)

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

    async def test_tool_error_text_stays_out_of_the_server_log(self, caplog: pytest.LogCaptureFixture) -> None:
        tools = create_autospec(WarehouseTools, instance=True)
        tools.run_sql.side_effect = WarehouseToolError("[guard:function] function md5 is not allowed: 'leak@x.com'")
        sdk_logger = logging.getLogger("mcp.server.mcpserver.server")
        level = sdk_logger.level

        try:
            with caplog.at_level(logging.INFO):
                async with Client(build_server(tools)) as client:
                    await client.call_tool("run_sql", {"sql": COUNT_ORDERS})
                    unconfigured = caplog.text
                    configure_logging()
                    caplog.clear()
                    result = await client.call_tool("run_sql", {"sql": COUNT_ORDERS})
        finally:
            sdk_logger.setLevel(level)

        assert "leak@x.com" in unconfigured  # what the SDK logs on its own
        assert "leak@x.com" in _text(result)
        assert "leak@x.com" not in caplog.text

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
        {"jsonrpc": "2.0", "id": TOOLS_LIST_ID, "method": "tools/list"},
    )
    server = subprocess.Popen(
        [stdio_server.command, *stdio_server.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        cwd=stdio_server.cwd,
        env=get_default_environment() | (stdio_server.env or {}),
    )
    assert server.stdin is not None
    assert server.stdout is not None
    watchdog = threading.Timer(STDIO_TIMEOUT_SECONDS, server.kill)
    watchdog.start()
    try:
        server.stdin.write("".join(json.dumps(request) + "\n" for request in requests))
        server.stdin.flush()

        # stdin stays open until the tools/list answer arrives: closing it earlier ends the server before it answers.
        messages: list[dict[str, Any]] = []
        for line in server.stdout:
            messages.append(json.loads(line))
            if messages[-1].get("id") == TOOLS_LIST_ID:
                break

        # Ending stdin is the stdio shutdown signal; whatever the server still prints must be protocol too.
        server.stdin.close()
        messages += [json.loads(line) for line in server.stdout]
        server.wait(timeout=STDIO_TIMEOUT_SECONDS)
    finally:
        watchdog.cancel()
        server.kill()

    assert messages
    assert all(message["jsonrpc"] == "2.0" for message in messages)
    responses = {message["id"]: message for message in messages if "id" in message}
    assert {tool["name"] for tool in responses[TOOLS_LIST_ID]["result"]["tools"]} == TOOL_NAMES


@pytest.mark.warehouse
def test_server_exits_at_startup_when_the_warehouse_is_unreachable(
    stdio_server: StdioServerParameters, closed_port: int
) -> None:
    env = get_default_environment() | (stdio_server.env or {}) | {"SHERPA_DB_PORT": str(closed_port)}

    # The server gives up after its 15 s wait; this bound fails the test if it hangs instead.
    completed = subprocess.run(
        [stdio_server.command, *stdio_server.args],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=stdio_server.cwd,
        env=env,
        timeout=STARTUP_FAILURE_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 1
    assert f":{closed_port} as " in completed.stderr
    assert "cannot connect to the warehouse at" in completed.stderr
    assert completed.stdout == ""
    assert env["SHERPA_AGENT_PASSWORD"] not in completed.stderr


def test_startup_waits_15_seconds_for_the_warehouse() -> None:
    assert STARTUP_WAIT_SECONDS == 15


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_leaves_the_sdks_own_protection(host: str) -> None:
    assert _transport_security(host) is None


def test_other_binds_allow_loopback_and_the_compose_service_name() -> None:
    assert _transport_security("0.0.0.0") == TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "mcp:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", "http://mcp:*"],
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("host", "origin", "status"),
    [
        ("127.0.0.1:8765", None, None),
        ("localhost:8765", None, None),
        ("[::1]:8765", None, None),
        ("mcp:8765", None, None),
        ("evil.example:8765", None, 421),
        ("127.0.0.1.evil.example:8765", None, 421),
        ("mcp", None, 421),
        ("localhost:8765", "http://localhost:5173", None),
        ("localhost:8765", "http://evil.example:5173", 403),
    ],
)
async def test_other_binds_check_host_and_origin_as_the_sdk_does(
    host: str, origin: str | None, status: int | None
) -> None:
    headers = [(b"host", host.encode()), (b"content-type", b"application/json")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = Request({"type": "http", "method": "POST", "headers": headers})
    middleware = TransportSecurityMiddleware(_transport_security("0.0.0.0"))

    response = await middleware.validate_request(request, is_post=True)

    assert (response.status_code if response is not None else None) == status


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
