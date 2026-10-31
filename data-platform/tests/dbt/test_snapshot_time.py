"""The wall-clock value dbt's snapshot materialization writes for an invalidated (hard-deleted) row (spec §6.3).

Needs Postgres and `.env`; the test reads one expression and writes nothing (artifacts go to `target-scratch`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from dbt.cli.main import dbtRunnerResult

pytestmark = pytest.mark.integration


def test_snapshot_wall_clock_is_timezone_aware_like_every_other_time_column(
    dbt: Callable[..., dbtRunnerResult],
) -> None:
    sql = "select pg_typeof({{ snapshot_get_time() }})::text as t"

    result = dbt("show", "--target", "prod", "--target-path", "target-scratch", "--quiet", "--inline", sql)

    assert result.result.results[0].agate_table.rows[0]["t"] == "timestamp with time zone"
