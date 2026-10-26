"""The wall-clock value dbt's snapshot materialization writes for an invalidated (hard-deleted) row (spec §6.3).

Needs Postgres and `.env`; the test reads one expression and writes nothing (artifacts go to `target-scratch`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DBT_DIR = Path(__file__).parents[2] / "dbt"
ENV_FILE = "../../.env"  # relative to DBT_DIR: uv mishandles an absolute path with spaces
DBT_TIMEOUT_SECONDS = 600

pytestmark = pytest.mark.integration


def test_snapshot_wall_clock_is_timezone_aware_like_every_other_time_column() -> None:
    sql = "select pg_typeof({{ snapshot_get_time() }})::text as t"

    args = ["--target", "prod", "--target-path", "target-scratch", "--quiet", "--output", "json", "--inline", sql]

    completed = subprocess.run(
        ["uv", "run", "--env-file", ENV_FILE, "dbt", "show", *args],
        cwd=DBT_DIR,
        env={**os.environ, "DBT_PROFILES_DIR": "."},
        check=True,
        capture_output=True,
        text=True,
        timeout=DBT_TIMEOUT_SECONDS,
    )

    assert '"t": "timestamp with time zone"' in completed.stdout, completed.stdout
