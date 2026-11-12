"""`v0_baseline` (agent spec §5.4): one agent with all the tools for `plan`, a fresh one for `answer`."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext

from sherpa_agent.guards import check_draft_sql, check_execution_reference, numbers_message, unverified_numbers
from sherpa_agent.models import model_settings
from sherpa_agent.outcomes import (
    Alternative,
    Answered,
    Draft,
    GroundedBy,
    Interpretation,
    InterpretationItem,
    ItemKind,
    NeedsClarification,
    SqlRevision,
    Step,
    StepName,
    Unanswerable,
    UsageSnapshot,
)
from sherpa_agent.recording import Execution, RecordingToolset, plan_tools
from sherpa_agent.variants.base import OUTPUT_MODE, OUTPUT_RETRIES, VariantContext

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import AgentRunResult
    from pydantic_ai.settings import ModelSettings

    from sherpa_agent.outcomes import AnswerOutcome, Correction, PlanOutcome

NAME: Final = "v0_baseline"
GET_CONTEXT: Final = "get_context"
# §5.1: the step an item's grounding came from
STEP_OF_GROUNDING: Final[dict[GroundedBy, StepName]] = {
    "search_values": "values",
    "accepted_values": "schema",
    "literal": "sql",
    "default": "scope",
    "correction": "scope",
}
TOOL_RETRIES: Final = 4  # §5.3: above the SQL-attempt limit, so the framework never ends a run first
SQL_REVISION_REASON: Final = "the answer step ran different SQL from the approved draft"

# Prompt sections, in the order the plan prompt shows them (spec §5.1, §5.3).
ROLE = (
    "You are SQL Sherpa, an analyst for a synthetic e-commerce data warehouse (Postgres, the `marts` schema). "
    "You only read data: you never write, and you only use the tools you are given."
)
SCOPE_TEMPLATE = (
    "Warehouse scope (from get_context):\n"
    "- as_of_date: {as_of_date}\n"
    "- covers: {covers}\n"
    "- does not cover: {does_not_cover}\n"
    "- notes: {notes}"
)
CORRECTIONS_HEADER = (
    "Corrections from the user. They are facts you must use, they override your own reading of the question, "
    "and each applies to the whole run:\n"
)
DEFAULTS = (
    "Defaults, never ask the user about these and record them as assumptions with grounded_by default:\n"
    '- "revenue" means `net_revenue`.\n'
    '- "sales" means `gross_revenue`.\n'
    '- "customers" means non-deleted customers with at least one non-cancelled order, unless stated otherwise.\n'
    "- Resolve relative periods against `as_of_date`, with calendar periods: "
    '"last quarter" is the previous calendar quarter.'
)
CLARIFY_OR_REFUSE = (
    "Ask the user to clarify only when no documented default applies and the readings would lead to materially "
    '*different* queries (for example "best customers": by revenue or by order count?). '
    "Refuse as unanswerable when the question is outside what get_context says the warehouse covers, or when it "
    "asks for a write."
)
DERIVED_FIGURES = (
    "Compute derived figures (shares, differences, growth) in SQL. The final answer may use only numbers that are "
    "in the result of the query."
)
CONVENTIONS = (
    "Conventions:\n"
    "- Do not round in SQL; round in the prose.\n"
    "- Name a period by its first date.\n"
    "- Return durations as numbers.\n"
    "- Return one row per group.\n"
    '- `as_of_date` is "today".\n'
    "- Match names case-insensitively with `lower(column) = 'literal'`."
)
PLAN_TASK = (
    "Your task is to plan the question, not to answer it: you cannot run queries. Look up the metrics, tables, "
    "columns and values you need, write one SQL query, and run it through validate_sql until it passes. Return "
    "exactly the SQL that last passed validate_sql, with the interpretation items behind it: every metric, filter, "
    "time range, grain, table and assumption, each with how it was grounded (search_values, accepted_values, "
    "literal, default or correction). Or ask for clarification, or refuse."
)
ANSWER_TASK = (
    "Your task is to answer the question from the approved plan. Run the approved SQL with run_sql, exactly as "
    "given; change it only when it fails or cannot answer the question, and say so in the caveats. Then write "
    "the answer in plain prose from the result of the last query you ran, and give that query's execution_id. "
    "Describe the last query you ran. State in the caveats why an empty result is plausible or suspicious, and "
    "that a truncated result is truncated: never claim a total over truncated rows."
)
PLAN_INSTRUCTIONS = "\n\n".join(
    ["{role}", "{corrections}{defaults}", CLARIFY_OR_REFUSE, DERIVED_FIGURES, CONVENTIONS, PLAN_TASK]
)
ANSWER_INSTRUCTIONS = "\n\n".join(["{role}", DEFAULTS, DERIVED_FIGURES, CONVENTIONS, ANSWER_TASK])


class ItemOut(BaseModel):
    """One interpretation item: what the SQL assumes and how that was grounded."""

    model_config = ConfigDict(frozen=True)

    kind: ItemKind
    value: str
    grounded_by: GroundedBy
    alternatives: tuple[Alternative, ...] = ()


class PlanDraftOut(BaseModel):
    """The plan is ready: SQL that passed validate_sql and the interpretation behind it."""

    model_config = ConfigDict(frozen=True)

    sql: str
    items: tuple[ItemOut, ...]


class ClarifyOut(BaseModel):
    """No documented default applies and the readings lead to different queries: ask the user."""

    model_config = ConfigDict(frozen=True)

    question_to_user: str
    candidate_interpretations: tuple[str, ...]
    partial_items: tuple[ItemOut, ...]


class UnanswerableOut(BaseModel):
    """The question is outside the warehouse's scope, or asks for a write."""

    model_config = ConfigDict(frozen=True)

    reason: str
    closest_supported: str | None


class AnswerOut(BaseModel):
    """The answer in prose, and the execution_id of the query it describes."""

    model_config = ConfigDict(frozen=True)

    answer_text: str
    caveats: tuple[str, ...]
    execution_id: str


type PlanResult = PlanDraftOut | ClarifyOut | UnanswerableOut


class V0Baseline:
    name = NAME

    async def plan(self, question: str, corrections: Sequence[Correction], ctx: VariantContext) -> PlanOutcome:
        recorder = RecordingToolset(ctx.toolset, on_progress=ctx.on_progress)
        ctx.recorders.append(recorder)
        async with ctx.toolset:
            scope = await _scope(ctx)
            result = await self._run_plan(question, corrections, ctx, recorder, scope)
        steps = await recorder.finish()
        match result.output:
            case PlanDraftOut() as drafted:
                return Draft(
                    outcome="draft",
                    draft_id=uuid.uuid4().hex,
                    question=question,
                    corrections=tuple(corrections),
                    interpretation=_interpretation(drafted.items, steps),
                    sql=recorder.last_validated_sql or drafted.sql,
                    steps=steps,
                    usage=_snapshot(ctx),
                )
            case ClarifyOut() as clarifying:
                return NeedsClarification(
                    outcome="needs_clarification",
                    question_to_user=clarifying.question_to_user,
                    candidate_interpretations=clarifying.candidate_interpretations,
                    partial=_interpretation(clarifying.partial_items, steps)
                    if clarifying.partial_items and steps
                    else None,
                )
            case UnanswerableOut() as refusal:
                return Unanswerable(
                    outcome="unanswerable", reason=refusal.reason, closest_supported=refusal.closest_supported
                )

    async def _run_plan(
        self,
        question: str,
        corrections: Sequence[Correction],
        ctx: VariantContext,
        recorder: RecordingToolset,
        scope: _Scope,
    ) -> AgentRunResult[PlanResult]:
        agent: Agent[None, PlanResult] = Agent(
            ctx.model_for(ctx.settings.model_default),
            output_type=OUTPUT_MODE([PlanDraftOut, ClarifyOut, UnanswerableOut]),
            instructions=_plan_instructions(corrections, scope),
            toolsets=[plan_tools(recorder)],
            model_settings=_model_settings(ctx),
            retries={"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES},
        )

        @agent.output_validator
        def check_sql(output: PlanResult) -> PlanResult:
            if isinstance(output, PlanDraftOut) and (
                problem := check_draft_sql(output.sql, recorder.last_validated_sql)
            ):
                raise ModelRetry(problem)
            return output

        return await agent.run(question, usage=ctx.usage, usage_limits=ctx.usage_limits())

    async def answer(self, draft: Draft, ctx: VariantContext) -> AnswerOutcome:
        recorder = RecordingToolset(ctx.toolset)
        ctx.recorders.append(recorder)
        async with ctx.toolset:
            scope = await _scope(ctx)
            agent = _answer_agent(draft, ctx, recorder, scope)
            result = await agent.run(_answer_prompt(draft), usage=ctx.usage, usage_limits=ctx.usage_limits())
        await recorder.finish()
        execution = next(e for e in recorder.executions if e.execution_id == result.output.execution_id)
        return Answered(
            outcome="answered",
            answer_text=result.output.answer_text,
            interpretation=draft.interpretation,
            execution_id=execution.execution_id,
            sql=execution.sql,
            columns=execution.columns,
            rows=execution.rows,
            returned_rows=execution.returned_rows,
            truncated=execution.truncated,
            caveats=result.output.caveats,
            sql_revision=_sql_revision(draft, execution),
        )


class _Scope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of_date: date
    covers: tuple[str, ...]
    does_not_cover: tuple[str, ...]
    notes: tuple[str, ...]

    def text(self) -> str:
        return SCOPE_TEMPLATE.format(
            as_of_date=self.as_of_date.isoformat(),
            covers="; ".join(self.covers) or "-",
            does_not_cover="; ".join(self.does_not_cover) or "-",
            notes="; ".join(self.notes) or "-",
        )


def _plan_instructions(corrections: Sequence[Correction], scope: _Scope) -> str:
    block = ""
    if corrections:
        block = CORRECTIONS_HEADER + "\n".join(f"- {_correction_line(c)}" for c in corrections) + "\n\n"
    return PLAN_INSTRUCTIONS.format(role=f"{ROLE}\n\n{scope.text()}", corrections=block, defaults=DEFAULTS)


def _correction_line(correction: Correction) -> str:
    match correction.action, correction.original:
        case "replace", target if target is not None:
            return (
                f"replace {target.kind} '{target.value}' with '{correction.value}' "
                "(record it with grounded_by correction)"
            )
        case "remove", target if target is not None:
            return f"do not apply {target.kind} '{target.value}'"
        case _:
            return correction.value or ""


def _model_settings(ctx: VariantContext) -> ModelSettings:
    return model_settings(ctx.settings.model_default, ctx.settings.effort_default)


def _answer_agent(
    draft: Draft, ctx: VariantContext, recorder: RecordingToolset, scope: _Scope
) -> Agent[None, AnswerOut]:
    agent: Agent[None, AnswerOut] = Agent(
        ctx.model_for(ctx.settings.model_default),
        output_type=OUTPUT_MODE(AnswerOut),
        instructions=ANSWER_INSTRUCTIONS.format(role=f"{ROLE}\n\n{scope.text()}"),
        toolsets=[recorder],
        model_settings=_model_settings(ctx),
        retries={"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES},
    )
    time_range = [item.value for item in draft.interpretation.items if item.kind == "time_range"]

    @agent.output_validator
    def check_numbers(output: AnswerOut) -> AnswerOut:
        if problem := check_execution_reference(output.execution_id, recorder.executions):
            raise ModelRetry(problem)
        execution = next(e for e in recorder.executions if e.execution_id == output.execution_id)
        unverified = unverified_numbers(
            output.answer_text, execution, question=draft.question, time_range=time_range, as_of_date=scope.as_of_date
        )
        if unverified:
            raise ModelRetry(numbers_message(unverified))
        return output

    return agent


async def _scope(ctx: VariantContext) -> _Scope:
    """One direct call, not one of the model's: the prompt and the numbers check must not depend on what it asked."""
    run_ctx = RunContext(deps=None, model=ctx.model_for(ctx.settings.model_default), usage=ctx.usage)
    tools = await ctx.toolset.get_tools(run_ctx)
    scope = await ctx.toolset.call_tool(GET_CONTEXT, {}, run_ctx, tools[GET_CONTEXT])
    return _Scope.model_validate(scope)


def _answer_prompt(draft: Draft) -> str:
    items = "\n".join(f"- {item.kind}: {item.value}" for item in draft.interpretation.items)
    return f"Question: {draft.question}\n\nInterpretation:\n{items}\n\nApproved SQL:\n{draft.sql}"


def _sql_revision(draft: Draft, execution: Execution) -> SqlRevision | None:
    if check_draft_sql(execution.sql, draft.sql) is None:
        return None
    return SqlRevision(previous_sql=draft.sql, reason=SQL_REVISION_REASON)


def _interpretation(items: Sequence[ItemOut], steps: Sequence[Step]) -> Interpretation:
    return Interpretation(
        items=tuple(
            InterpretationItem(
                kind=item.kind,
                value=item.value,
                step_id=_step_id(item.grounded_by, steps),
                grounded_by=item.grounded_by,
                alternatives=item.alternatives,
            )
            for item in items
        )
    )


def _step_id(grounded_by: GroundedBy, steps: Sequence[Step]) -> str:
    """The last completed step of the grounding's purpose, else the last step."""
    purpose = STEP_OF_GROUNDING[grounded_by]
    return next((s.step_id for s in reversed(steps) if s.name == purpose), steps[-1].step_id)


def _snapshot(ctx: VariantContext) -> UsageSnapshot:
    usage = ctx.usage
    return UsageSnapshot(
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        tool_calls=usage.tool_calls,
    )
