from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import psycopg
from pydantic import BaseModel, ConfigDict
from sherpa_mcp.catalog import CatalogUnavailable

if TYPE_CHECKING:
    from sherpa_mcp.catalog import MartCatalogLoader

    from sherpa_ui.settings import UiSettings

logger = logging.getLogger(__name__)


class ArtifactsHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "missing"]
    generated_at: datetime | None


class Health(BaseModel):
    """What ui-1 can vouch for; the tool server and the model join in ui-4."""

    model_config = ConfigDict(frozen=True)

    database: Literal["ok", "down"]
    artifacts: ArtifactsHealth


class HealthService:
    """Blocking (a connection and a file read): call `check` from a worker thread."""

    def __init__(self, settings: UiSettings, loader: MartCatalogLoader) -> None:
        self._settings = settings
        self._loader = loader

    def check(self) -> Health:
        return Health(database=self._database(), artifacts=self._artifacts())

    def _database(self) -> Literal["ok", "down"]:
        try:
            with psycopg.connect(self._settings.ui_conninfo(), autocommit=True) as conn:
                conn.execute("select 1")
        except psycopg.Error as error:
            logger.warning("health: database is down: %s", error)
            return "down"

        return "ok"

    def _artifacts(self) -> ArtifactsHealth:
        try:
            return ArtifactsHealth(status="ok", generated_at=self._loader.load().generated_at)
        except CatalogUnavailable as error:
            logger.warning("health: dbt artifacts unavailable: %s", error)
            return ArtifactsHealth(status="missing", generated_at=None)
