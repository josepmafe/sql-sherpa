from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.selector import SelectorEvaluator

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parents[4]
DBT_PROJECT = REPO_ROOT / "data-platform" / "dbt"
DBT_TIMEOUT_SECONDS = 300
GRAPH_RESOURCE_TYPES = ("model", "seed", "snapshot", "source", "exposure")
SELECTORS = [
    "fct_orders",
    "+fct_orders",
    "fct_orders+",
    "1+fct_orders",
    "fct_orders+2",
    "2+fct_orders+1",
    "source:shop",
    "source:shop.orders+",
    "orders",
    "config.meta.domain:sales",
    "+config.meta.domain:sales",
    "config.meta.domain:shared+",
    "fct_orders dim_date",
    "+dim_customers fct_orders+",
    "no_such_model",
    "0+fct_orders",
]


@pytest.fixture(scope="session")
def target_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fresh `dbt parse` into a temporary target path, so a stale `target-prod` cannot fail the test."""
    path = tmp_path_factory.mktemp("dbt-target")
    _dbt("parse", path)
    return path


@pytest.fixture(scope="session")
def evaluator(target_path: Path) -> SelectorEvaluator:
    manifest = json.loads((target_path / "manifest.json").read_text(encoding="utf-8"))
    return SelectorEvaluator(ManifestGraph.from_manifest(manifest))


@pytest.mark.integration
@pytest.mark.parametrize("selector", SELECTORS)
def test_matches_dbt_ls(evaluator: SelectorEvaluator, target_path: Path, selector: str) -> None:
    resource_types = [arg for resource_type in GRAPH_RESOURCE_TYPES for arg in ("--resource-type", resource_type)]
    output = _dbt(
        "ls", target_path, "--select", selector, *resource_types, "--output", "json", "--output-keys", "unique_id"
    )

    expected = {
        json.loads(line)["unique_id"] for line in output.splitlines() if line.startswith("{") and "unique_id" in line
    }

    assert evaluator.select(selector) == expected


def _dbt(command: str, target_path: Path, *args: str) -> str:
    arguments: Sequence[str] = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        command,
        "--project-dir",
        str(DBT_PROJECT),
        "--profiles-dir",
        str(DBT_PROJECT),
        "--target",
        "prod",
        "--target-path",
        str(target_path),
        *args,
    ]
    result = subprocess.run(
        arguments, cwd=REPO_ROOT, capture_output=True, text=True, timeout=DBT_TIMEOUT_SECONDS, check=False
    )
    assert result.returncode == 0, f"dbt {command} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout
