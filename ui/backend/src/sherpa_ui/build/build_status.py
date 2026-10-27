"""The last full prod build, read from `ops.dbt_run_results` (data platform spec §8, written by the
`record_run_results` hook). Lineage uses it for node status; ui-3 reuses `LAST_FULL_BUILD_SQL` for the data-status
strip."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final

import psycopg
from psycopg import errors
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from sherpa_ui.settings import UiSettings

logger = logging.getLogger(__name__)

# The rows of the newest invocation (by when it started) that was a full `dbt build` on prod; the invocation id breaks
# a tie between two that started in the same instant.
LAST_FULL_BUILD_SQL: Final = """
select invocation_id, run_started_at, unique_id, resource_type, status, failures, execution_time_s, message
from ops.dbt_run_results
where invocation_id = (
    select invocation_id
    from ops.dbt_run_results
    where target_name = 'prod' and command = 'build' and is_full_run
    group by invocation_id
    order by max(run_started_at) desc, invocation_id desc
    limit 1
)
"""
UNAVAILABLE_MESSAGE: Final = "the warehouse is unreachable ({reason}); is `make up` running?"


class BuildStatusUnavailable(Exception):
    """The database holding the build record cannot be reached."""


class NodeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    unique_id: str
    resource_type: str
    status: str
    failures: int | None
    execution_time_s: float | None
    message: str | None


class LastFullBuild(BaseModel):
    model_config = ConfigDict(frozen=True)

    invocation_id: str
    started_at: datetime
    results: Mapping[str, NodeResult]

    @property
    def node_count(self) -> int:
        return len(self.results)


class BuildStatusReader:
    """Blocking: call it from a worker thread."""

    def __init__(self, settings: UiSettings) -> None:
        self._settings = settings

    def last_full_build(self) -> LastFullBuild | None:
        """None when no full prod build is recorded, or when `ops` (or its table) does not exist yet."""
        try:
            with psycopg.connect(self._settings.ui_conninfo(), autocommit=True) as conn:
                rows = conn.execute(LAST_FULL_BUILD_SQL).fetchall()
        except (errors.UndefinedTable, errors.InvalidSchemaName):
            return None
        except psycopg.OperationalError as error:
            raise BuildStatusUnavailable(UNAVAILABLE_MESSAGE.format(reason=_first_line(error))) from error

        if not rows:
            return None

        results = {
            row[2]: NodeResult(
                unique_id=row[2],
                resource_type=row[3],
                status=row[4],
                failures=row[5],
                execution_time_s=row[6],
                message=row[7],
            )
            for row in rows
        }
        return LastFullBuild(invocation_id=rows[0][0], started_at=rows[0][1], results=results)


def _first_line(error: Exception) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__
