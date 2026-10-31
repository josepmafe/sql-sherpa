from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict
from sherpa_mcp.settings import McpSettings


@pytest.fixture
def agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHERPA_DB_HOST", "db.example")
    monkeypatch.setenv("SHERPA_DB_PORT", "5433")
    monkeypatch.setenv("SHERPA_DB_NAME", "sherpa")
    monkeypatch.setenv("SHERPA_AGENT_USER", "sherpa_agent")
    monkeypatch.setenv("SHERPA_AGENT_PASSWORD", "s3cret pw")
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)


@pytest.mark.usefixtures("agent_env")
class TestMcpSettings:
    def test_agent_conninfo_forces_read_only_and_timeout(self) -> None:
        settings = McpSettings(_env_file=None)

        info = conninfo_to_dict(settings.agent_conninfo())

        assert info["options"] == "-c default_transaction_read_only=on -c statement_timeout=5s"
        assert info["connect_timeout"] == "10"
        assert info["user"] == "sherpa_agent"
        assert info["password"] == "s3cret pw"
        assert info["host"] == "db.example"
        assert info["port"] == "5433"

    def test_transport_reads_unprefixed_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TRANSPORT", "http")

        settings = McpSettings(_env_file=None)

        assert settings.transport == "http"

    def test_transport_ignores_prefixed_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_TRANSPORT", "http")

        settings = McpSettings(_env_file=None)

        assert settings.transport == "stdio"

    def test_pool_holds_4_connections_by_default(self) -> None:
        settings = McpSettings(_env_file=None)

        assert settings.mcp_pool_size == 4
