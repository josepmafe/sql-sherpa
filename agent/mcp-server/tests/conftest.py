from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import psycopg
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.database import AgentDatabase, QueryResult
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.settings import McpSettings
from sherpa_mcp.tools import WarehouseTools

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_PATH = REPO_ROOT / "agent" / "mcp-server" / "src" / "sherpa_mcp" / "examples.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
# Data platform spec §6.5: the ten marts.
MARTS = (
    "dim_customers",
    "dim_date",
    "dim_products",
    "dim_promotions",
    "fct_order_discounts",
    "fct_order_items",
    "fct_orders",
    "fct_refunds",
    "fct_returns",
    "fct_sessions",
)
LOCALHOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # what the server binds in the container
CONTAINER_URL = "http://127.0.0.1:8765/mcp"  # the compose `mcp` service
SERVER_START_TIMEOUT_SECONDS = 60
# The variables the server needs, and only those (no admin login); the SDK's stdio client passes only a minimal
# environment (agent spec §3).
SERVER_ENV_PREFIXES = ("SHERPA_DB_", "SHERPA_AGENT_", "SHERPA_MCP_", "MCP_")
SERVER_ENV_NAMES = ("SHERPA_DBT_TARGET_PATH", "PATH", "SYSTEMROOT")
ROLE_SETTINGS = "select setdatabase, setrole::regrole::text, setconfig from pg_db_role_setting order by 1, 2"


class AdminSettings(BaseSettings):
    """The admin login the warehouse tests use for setup checks, read from `.env`."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    admin_user: str
    admin_password: SecretStr

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.admin_user,
            password=self.admin_password.get_secret_value(),
        )


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def examples_path() -> Path:
    return EXAMPLES_PATH


@pytest.fixture(scope="session")
def marts() -> tuple[str, ...]:
    """The ten marts of data platform spec §6.5."""
    return MARTS


@pytest.fixture
def fixture_target(tmp_path: Path) -> Path:
    """A dbt target folder with copies of `fixtures/*.json`: three marts, one of them with its primary key in the
    middle of its columns, two with a PII text column. The manifest has the shape of dbt 1.12.6 (checked against
    `make build-prod`'s on 2026-10-30): columns and models carry `meta` and `config.meta`."""
    shutil.copy(FIXTURES / "manifest.json", tmp_path)
    shutil.copy(FIXTURES / "catalog.json", tmp_path)
    return tmp_path


@pytest.fixture
def fake_database() -> AgentDatabase:
    """An `AgentDatabase` that answers nothing until a test sets the canned `QueryResult`s of `fetch`,
    `fetch_for_model` and `scalar`; a call with the wrong signature fails (autospec)."""
    database = create_autospec(AgentDatabase, instance=True)
    empty = QueryResult(columns=(), rows=(), returned_rows=0, truncated=False, elapsed_ms=0)
    database.fetch.return_value = empty
    database.fetch_for_model.return_value = empty
    return database  # type: ignore[no-any-return]


@pytest.fixture
def fake_tools(fixture_target: Path, fake_database: AgentDatabase) -> WarehouseTools:
    return WarehouseTools(MartCatalogLoader(fixture_target), fake_database, ExampleIndex([]))


@pytest.fixture(scope="session")
def warehouse_settings() -> McpSettings:
    return McpSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture(scope="session")
def admin_conninfo() -> str:
    return AdminSettings().conninfo()  # type: ignore[call-arg]


@pytest.fixture(scope="session", autouse=True)
def role_settings_unchanged(request: pytest.FixtureRequest) -> Iterator[None]:
    """The agent's `pg_db_role_setting` rows are the same after the whole warehouse run (agent spec §4.2 item 5).

    Active whenever any warehouse test is selected, whatever `-k` picks; a change fails the run at teardown.
    """
    if not any(item.get_closest_marker("warehouse") for item in request.session.items):
        yield
        return

    admin_conninfo: str = request.getfixturevalue("admin_conninfo")
    agent_user = request.getfixturevalue("warehouse_settings").agent_user
    before = _role_settings(admin_conninfo)
    agent_rows = [config for _, role, config in before if role == agent_user]
    assert agent_rows, f"{agent_user} has no role settings to protect; was the init script run?"
    assert "default_transaction_read_only=on" in agent_rows[0]

    yield

    assert _role_settings(admin_conninfo) == before, "the agent's role settings changed during the test run"


@pytest.fixture(scope="module")
def agent_database(warehouse_settings: McpSettings) -> Iterator[AgentDatabase]:
    database = AgentDatabase(warehouse_settings)
    yield database
    database.close()


@pytest.fixture
def admin(admin_conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="module")
def prod_loader(warehouse_settings: McpSettings) -> MartCatalogLoader:
    return MartCatalogLoader(REPO_ROOT / warehouse_settings.dbt_target_path)


@pytest.fixture(scope="module")
def tools(prod_loader: MartCatalogLoader, agent_database: AgentDatabase) -> WarehouseTools:
    return WarehouseTools(prod_loader, agent_database, ExampleIndex.from_yaml(EXAMPLES_PATH))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def closed_port() -> int:
    """A loopback port nothing listens on: free a moment ago, and nothing here binds it."""
    return _free_port()


@pytest.fixture
def stdio_server() -> StdioServerParameters:
    """How a client starts `sherpa-mcp` over stdio, passing the server's variables explicitly (agent spec §3)."""
    return StdioServerParameters(command="uv", args=["run", "sherpa-mcp"], env=_server_env("stdio"), cwd=REPO_ROOT)


@pytest.fixture(params=["stdio", "http", "container"])
async def mcp_client(request: pytest.FixtureRequest) -> AsyncIterator[Client]:
    """A connected client to `sherpa-mcp`: a subprocess over each transport, and the compose `mcp` container."""
    server: StdioServerParameters | str
    match request.param:
        case "stdio":
            server = request.getfixturevalue("stdio_server")
        case "container":
            if os.environ.get("SHERPA_TEST_MCP_CONTAINER") != "1":
                pytest.skip("set SHERPA_TEST_MCP_CONTAINER=1 with the compose mcp service up")
            server = os.environ.get("MCP_HTTP_URL", CONTAINER_URL)
        case _:
            server = request.getfixturevalue("mcp_http_url")
    async with Client(server) as client:
        yield client


@pytest.fixture(scope="module")
def mcp_http_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """`sherpa-mcp` over Streamable HTTP on a free loopback port, killed on teardown."""
    with _http_server(LOCALHOST, tmp_path_factory) as port:
        yield f"http://{LOCALHOST}:{port}/mcp"


@pytest.fixture(scope="module")
def mcp_all_interfaces_port(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """`sherpa-mcp` over Streamable HTTP bound to 0.0.0.0, as in the container; yields its port."""
    with _http_server(ALL_INTERFACES, tmp_path_factory) as port:
        yield port


def _role_settings(admin_conninfo: str) -> list[tuple[object, ...]]:
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        return conn.execute(ROLE_SETTINGS).fetchall()


def _server_env(transport: str) -> dict[str, str]:
    """The test environment's server variables, with `MCP_TRANSPORT` set to `transport`; never the admin login."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper().startswith(SERVER_ENV_PREFIXES) or name.upper() in SERVER_ENV_NAMES
    }
    return env | {"MCP_TRANSPORT": transport}


@contextmanager
def _http_server(host: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    port = _free_port()
    command = shutil.which("sherpa-mcp")
    assert command is not None, "the sherpa-mcp console script is not installed; run uv sync"
    env = get_default_environment() | _server_env("http") | {"SHERPA_MCP_HOST": host, "SHERPA_MCP_PORT": str(port)}
    log_path = tmp_path_factory.mktemp("mcp-http") / "stderr.log"
    with log_path.open("w", encoding="utf-8") as log:
        # The console script itself, not `uv run`, so the killed process is the server and leaves no child behind.
        server = subprocess.Popen([command], cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            _wait_for_port(port, server, log_path)
            yield port
        finally:
            server.kill()
            server.wait(timeout=SERVER_START_TIMEOUT_SECONDS)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((LOCALHOST, 0))
        port: int = sock.getsockname()[1]
        return port


def _wait_for_port(port: int, server: subprocess.Popen[bytes], log_path: Path) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f"sherpa-mcp exited with {server.returncode}:\n{log_path.read_text(encoding='utf-8')}")
        try:
            with socket.create_connection((LOCALHOST, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)

    pytest.fail(f"sherpa-mcp did not listen on port {port} within {SERVER_START_TIMEOUT_SECONDS} s")
