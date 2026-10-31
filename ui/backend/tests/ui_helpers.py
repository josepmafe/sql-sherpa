"""Constants and builders the UI backend tests share (the generator's tests keep their own `helpers.py`)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sherpa_ui.build.build_status import LastFullBuild, NodeResult

REPO_ROOT = Path(__file__).resolve().parents[3]
STARTED_AT = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
# An aggregate, so no row comes back before the whole cross join is counted (UI spec §8): far longer than any test.
LONG_AGGREGATE = (
    "select count(*) as n from marts.fct_order_items a "
    "cross join marts.fct_order_items b cross join marts.fct_order_items c"
)


def make_node_result(unique_id: str, status: str, **overrides: object) -> NodeResult:
    """A row of a build; its resource type is the first part of the id (`model`, `test`, `seed`...)."""
    fields: dict[str, object] = {
        "unique_id": unique_id,
        "resource_type": unique_id.split(".")[0],
        "status": status,
        "failures": None,
        "execution_time_s": None,
        "message": None,
    }
    return NodeResult.model_validate({**fields, **overrides})


def make_last_full_build(*results: NodeResult, **overrides: object) -> LastFullBuild:
    fields: dict[str, object] = {
        "invocation_id": "inv",
        "started_at": STARTED_AT,
        "results": {result.unique_id: result for result in results},
    }
    return LastFullBuild.model_validate({**fields, **overrides})
