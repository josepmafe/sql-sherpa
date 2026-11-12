from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from sherpa_agent.outcomes import (
    Correction,
    CorrectionTarget,
    Draft,
    Failed,
    Interpretation,
    InterpretationItem,
    NeedsClarification,
    PlanOutcome,
    RunStats,
    Unanswerable,
    UsageSnapshot,
)

TARGET = CorrectionTarget(kind="filter", value="country = 'ES'")
PLAN_OUTCOME: TypeAdapter[PlanOutcome] = TypeAdapter(PlanOutcome)


def run_stats() -> RunStats:
    return RunStats(
        variant="v0_baseline", usage=(), latency_ms=10, cost_eur=None, tool_calls=2, sql_attempts=1, trace_id=None
    )


def make_draft(**overrides: Any) -> Draft:
    item = InterpretationItem(kind="filter", value="country = 'ES'", step_id="s2", grounded_by="search_values")
    fields: dict[str, Any] = {
        "outcome": "draft",
        "draft_id": "d1",
        "question": "Revenue in Spain?",
        "corrections": (),
        "interpretation": Interpretation(items=(item,)),
        "sql": "select 1",
        "steps": (),
        "usage": UsageSnapshot(requests=3, input_tokens=1200, output_tokens=340, tool_calls=4),
        "stats": run_stats(),
    }
    return Draft(**(fields | overrides))


class TestCorrection:
    @pytest.mark.parametrize(
        "fields",
        [
            {"action": "replace", "original": TARGET, "value": "country = 'FR'"},
            {"action": "remove", "original": TARGET, "value": None},
            {"action": "free_text", "original": None, "value": "use net revenue"},
        ],
    )
    def test_valid_combinations_are_accepted(self, fields: dict[str, Any]) -> None:
        assert Correction(**fields).action == fields["action"]

    @pytest.mark.parametrize(
        "fields",
        [
            {"action": "free_text", "original": TARGET, "value": "x"},
            {"action": "replace", "original": None, "value": "x"},
            {"action": "remove", "original": None, "value": None},
            {"action": "remove", "original": TARGET, "value": "x"},
            {"action": "replace", "original": TARGET, "value": None},
            {"action": "free_text", "original": None, "value": None},
        ],
    )
    def test_original_and_value_rules_are_enforced(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match=r"original|value"):
            Correction(**fields)


class TestPlanOutcome:
    @pytest.mark.parametrize(
        "outcome",
        [
            make_draft(),
            NeedsClarification(
                outcome="needs_clarification",
                question_to_user="Which year?",
                candidate_interpretations=("2024", "2025"),
                partial=None,
                stats=run_stats(),
            ),
            Unanswerable(outcome="unanswerable", reason="No such data", closest_supported=None, stats=run_stats()),
            Failed(outcome="failed", reason="budget", last_sql="select 1", last_error=None, stats=run_stats()),
        ],
    )
    def test_round_trips_through_json_by_its_discriminator(self, outcome: PlanOutcome) -> None:
        restored = PLAN_OUTCOME.validate_json(PLAN_OUTCOME.dump_json(outcome))

        assert restored == outcome
        assert type(restored) is type(outcome)

    def test_an_outcome_outside_the_plan_union_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="outcome"):
            PLAN_OUTCOME.validate_python({"outcome": "answered"})

    def test_a_draft_keeps_the_usage_snapshot_it_carries_forward(self) -> None:
        restored = PLAN_OUTCOME.validate_json(PLAN_OUTCOME.dump_json(make_draft()))

        assert isinstance(restored, Draft)
        assert restored.usage == UsageSnapshot(requests=3, input_tokens=1200, output_tokens=340, tool_calls=4)

    def test_an_outcome_a_variant_returns_has_no_stats_yet(self) -> None:
        outcome = Unanswerable(outcome="unanswerable", reason="r", closest_supported=None)

        assert outcome.stats is None
