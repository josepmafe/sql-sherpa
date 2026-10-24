from __future__ import annotations

from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr
from sherpa_generator.settings import CONNECT_TIMEOUT_SECONDS, GeneratorSettings


def _settings() -> GeneratorSettings:
    return GeneratorSettings(
        db_host="127.0.0.1", db_port=5433, db_name="sherpa", admin_user="admin", admin_password=SecretStr("pw")
    )


class TestGeneratorSettings:
    def test_conninfo_bounds_the_connect_wait(self) -> None:
        info = conninfo_to_dict(_settings().conninfo())

        assert info["connect_timeout"] == str(CONNECT_TIMEOUT_SECONDS)
        assert info["dbname"] == "sherpa"

    def test_conninfo_dbname_override_wins(self) -> None:
        info = conninfo_to_dict(_settings().conninfo("scratch"))

        assert info["dbname"] == "scratch"
        assert info["host"] == "127.0.0.1"
