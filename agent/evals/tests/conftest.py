from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sherpa_evals.cases import GoldCase
from sherpa_evals.warehouse import selector_event_loop

if TYPE_CHECKING:
    from collections.abc import Callable


def build_case(id: str = "values-001", **overrides: Any) -> GoldCase:
    category = id.rsplit("-", 1)[0]
    fields: dict[str, Any] = {
        "id": id,
        "category": category,
        "question": f"Question for {id}?",
        "tranche": 1,
        "expected_outcome": "answered",
        "gold_sql": "select count(*) from marts.fct_orders",
        "tables": ("fct_orders",),
        "rationale": "Counts orders.",
    }
    fields.update(overrides)
    return GoldCase(**fields)


@pytest.fixture
def make_case() -> Callable[..., GoldCase]:
    return build_case


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    return "asyncio", {"loop_factory": selector_event_loop}
