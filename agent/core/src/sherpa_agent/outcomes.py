"""The plan and answer contract (agent spec §5.1): frozen models, pure."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

ItemKind = Literal["metric", "filter", "time_range", "grain", "table", "assumption"]
GroundedBy = Literal["search_values", "accepted_values", "literal", "default", "correction"]
StepName = Literal["scope", "schema", "values", "sql", "answer"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Alternative(_Frozen):
    value: str
    label: str
    score: float | None


class InterpretationItem(_Frozen):
    kind: ItemKind
    value: str
    step_id: str
    grounded_by: GroundedBy
    alternatives: tuple[Alternative, ...] = ()


class Interpretation(_Frozen):
    items: tuple[InterpretationItem, ...]


class CorrectionTarget(_Frozen):
    kind: ItemKind
    value: str


class Correction(_Frozen):
    action: Literal["replace", "remove", "free_text"]
    original: CorrectionTarget | None
    value: str | None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if (self.original is None) != (self.action == "free_text"):
            raise ValueError("original is None exactly for free_text")
        if (self.value is None) != (self.action == "remove"):
            raise ValueError("value is None exactly for remove")
        return self


class ToolCall(_Frozen):
    tool: str
    arguments: dict[str, JsonValue]
    summary: str


class Step(_Frozen):
    step_id: str
    name: StepName
    tool_calls: tuple[ToolCall, ...]


class ModelUsage(_Frozen):
    model: str
    input_tokens: int
    output_tokens: int


class UsageSnapshot(_Frozen):
    """What a `Draft` carries forward so the answer's stats cover the whole question."""

    requests: int
    input_tokens: int
    output_tokens: int
    tool_calls: int


class RunStats(_Frozen):
    variant: str
    usage: tuple[ModelUsage, ...]
    latency_ms: int
    cost_eur: Decimal | None
    tool_calls: int
    sql_attempts: int
    trace_id: str | None


class Draft(_Frozen):
    outcome: Literal["draft"]
    draft_id: str
    question: str
    corrections: tuple[Correction, ...]
    interpretation: Interpretation
    sql: str
    steps: tuple[Step, ...]
    usage: UsageSnapshot
    stats: RunStats | None = None  # a variant returns it unset; AgentService sets it


class NeedsClarification(_Frozen):
    outcome: Literal["needs_clarification"]
    question_to_user: str
    candidate_interpretations: tuple[str, ...]
    partial: Interpretation | None
    stats: RunStats | None = None


class Unanswerable(_Frozen):
    outcome: Literal["unanswerable"]
    reason: str
    closest_supported: str | None
    stats: RunStats | None = None


class Failed(_Frozen):
    outcome: Literal["failed"]
    reason: str
    last_sql: str | None
    last_error: str | None
    stats: RunStats | None = None


class SqlRevision(_Frozen):
    previous_sql: str
    reason: str


class Answered(_Frozen):
    outcome: Literal["answered"]
    answer_text: str
    interpretation: Interpretation
    execution_id: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    returned_rows: int
    truncated: bool
    caveats: tuple[str, ...]
    sql_revision: SqlRevision | None
    stats: RunStats | None = None


PlanOutcome = Annotated[Draft | NeedsClarification | Unanswerable | Failed, Field(discriminator="outcome")]
AnswerOutcome = Annotated[Answered | Failed, Field(discriminator="outcome")]
