from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.database import AgentDatabase
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.settings import McpSettings
from sherpa_mcp.tools import WarehouseTools

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_PATH = REPO_ROOT / "agent" / "mcp-server" / "src" / "sherpa_mcp" / "examples.yaml"
LOCALHOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # what the server binds in the container
SERVER_START_TIMEOUT_SECONDS = 60
# The variables the server needs, and only those (no admin login); the SDK's stdio client passes only a minimal
# environment (agent spec §3).
SERVER_ENV_PREFIXES = ("SHERPA_DB_", "SHERPA_AGENT_", "SHERPA_MCP_", "MCP_")
SERVER_ENV_NAMES = ("SHERPA_DBT_TARGET_PATH", "PATH", "SYSTEMROOT")


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
def warehouse_settings() -> McpSettings:
    return McpSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture(scope="session")
def admin_conninfo() -> str:
    return AdminSettings().conninfo()  # type: ignore[call-arg]


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
def stdio_server() -> StdioServerParameters:
    """How a client starts `sherpa-mcp` over stdio, passing the server's variables explicitly (agent spec §3)."""
    return StdioServerParameters(command="uv", args=["run", "sherpa-mcp"], env=_server_env("stdio"), cwd=REPO_ROOT)


@pytest.fixture(params=["stdio", "http"])
async def mcp_client(request: pytest.FixtureRequest) -> AsyncIterator[Client]:
    """A connected client to a `sherpa-mcp` subprocess, over each transport."""
    server: StdioServerParameters | str
    match request.param:
        case "stdio":
            server = request.getfixturevalue("stdio_server")
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
