from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from dbt.cli.main import dbtRunner
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.selector import SelectorEvaluator
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
    from dbt.contracts.graph.manifest import Manifest

DBT_PROJECT = REPO_ROOT / "data-platform" / "dbt"
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
def dbt_args(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """The arguments of every dbt call, with a temporary target path so a stale `target-prod` cannot fail the test."""
    target_path = tmp_path_factory.mktemp("dbt-target")
    return [
        "--project-dir",
        str(DBT_PROJECT),
        "--profiles-dir",
        str(DBT_PROJECT),
        "--target",
        "prod",
        "--target-path",
        str(target_path),
    ]


@pytest.fixture(scope="session")
def parsed(dbt_args: list[str]) -> Manifest:
    """One `dbt parse` for the whole module, in-process: every `dbt ls` reuses its manifest."""
    result = dbtRunner().invoke(["parse", *dbt_args])
    assert result.success, f"dbt parse failed: {result.exception}"
    return cast("Manifest", result.result)


@pytest.fixture(scope="session")
def evaluator(parsed: Manifest) -> SelectorEvaluator:
    return SelectorEvaluator(ManifestGraph.from_manifest(parsed.writable_manifest().to_dict()))


@pytest.mark.warehouse  # needs the dbt packages installed (`make build-prod` does it), like the warehouse tests
@pytest.mark.parametrize("selector", SELECTORS)
def test_matches_dbt_ls(evaluator: SelectorEvaluator, parsed: Manifest, dbt_args: list[str], selector: str) -> None:
    resource_types = [arg for resource_type in GRAPH_RESOURCE_TYPES for arg in ("--resource-type", resource_type)]
    result = dbtRunner(manifest=parsed).invoke(
        ["ls", *dbt_args, "--select", selector, *resource_types, "--output", "json", "--output-keys", "unique_id"]
    )
    assert result.success, f"dbt ls failed: {result.exception}"

    expected = {json.loads(line)["unique_id"] for line in cast("list[str]", result.result)}

    assert evaluator.select(selector) == expected
