"""What the runner needs from a variant (agent spec §7.2): a question in, an `Attempt` out. No agent types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.cases import GoldCase, GoldSet
    from sherpa_evals.settings import EvalSettings


class ModelUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    input_tokens: int
    output_tokens: int


class AttemptStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    latency_ms: int
    usage: tuple[ModelUsage, ...]
    tool_calls: int
    sql_attempts: int
    trace_id: str | None


class Attempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal["answered", "unanswerable", "needs_clarification", "failed"]
    sql: str | None  # the final executed SQL when answered
    reason: str | None
    stats: AttemptStats


class Answerer(Protocol):
    name: str

    async def attempt(self, question: str) -> Attempt: ...


NO_STATS: Final = AttemptStats(latency_ms=0, usage=(), tool_calls=0, sql_attempts=0, trace_id=None)


class OracleAnswerer:
    """Answers every question with its case's expected outcome and gold SQL; needs no model."""

    name = "oracle"

    def __init__(self, gold: GoldSet) -> None:
        self._cases: dict[str, GoldCase] = {case.question: case for case in gold.cases}

    async def attempt(self, question: str) -> Attempt:
        case = self._cases[question]
        return Attempt(outcome=case.expected_outcome, sql=case.gold_sql, reason=None, stats=NO_STATS)


# agent-design-3 adds the variants here.
ANSWERERS: dict[str, Callable[[GoldSet, EvalSettings], Answerer]] = {
    "oracle": lambda gold, settings: OracleAnswerer(gold),
}
