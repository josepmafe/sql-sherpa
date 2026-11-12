from __future__ import annotations

import pytest
from sherpa_agent.settings import AgentSettings

SERVER_VARIABLES = {
    "SHERPA_DB_HOST": "127.0.0.1",
    "SHERPA_DB_PORT": "5432",
    "SHERPA_DB_NAME": "sherpa",
    "SHERPA_AGENT_USER": "sherpa_agent",
    "SHERPA_AGENT_PASSWORD": "pw",
}
AMBIENT_VARIABLES = ("AWS_PROFILE", "AWS_REGION", "MCP_TRANSPORT", "MCP_HTTP_URL", "OTEL_EXPORTER_OTLP_ENDPOINT")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's own `.env` and shell must not leak into the settings under test."""
    for name in AMBIENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in SERVER_VARIABLES.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> AgentSettings:
    return AgentSettings(_env_file=None)  # type: ignore[call-arg]
