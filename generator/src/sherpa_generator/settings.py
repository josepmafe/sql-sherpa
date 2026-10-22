from __future__ import annotations

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneratorSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    admin_user: str
    admin_password: SecretStr

    def conninfo(self, dbname: str | None = None) -> str:
        """libpq connection string for the admin role; `dbname` overrides the configured database."""
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=dbname or self.db_name,
            user=self.admin_user,
            password=self.admin_password.get_secret_value(),
        )
