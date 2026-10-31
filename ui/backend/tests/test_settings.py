from __future__ import annotations

from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict
from pydantic import ValidationError
from sherpa_ui.settings import UiSettings


@pytest.mark.usefixtures("ui_env")
class TestUiSettings:
    def test_ui_conninfo_forces_read_only_and_the_ui_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_UI_STATEMENT_TIMEOUT_S", "12")

        settings = UiSettings(_env_file=None)

        info = conninfo_to_dict(settings.ui_conninfo())
        assert info["options"] == "-c default_transaction_read_only=on -c statement_timeout=12s"
        assert info["connect_timeout"] == "10"
        assert info["user"] == "sherpa_ui"
        assert info["password"] == "s3cret pw"
        assert info["host"] == "db.example"
        assert info["port"] == "5433"
        assert info["dbname"] == "sherpa"

    def test_zero_statement_timeout_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Postgres reads statement_timeout=0 as "no timeout at all".
        monkeypatch.setenv("SHERPA_UI_STATEMENT_TIMEOUT_S", "0")

        with pytest.raises(ValidationError, match="ui_statement_timeout_s"):
            UiSettings(_env_file=None)

    def test_target_path_ignores_the_mcp_servers_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_DBT_TARGET_PATH", "/somewhere/else")

        settings = UiSettings(_env_file=None)

        assert settings.ui_dbt_target_path == Path("data-platform/dbt/target-prod")
