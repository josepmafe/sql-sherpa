"""Outcomes as Rich renderables (agent spec §5.5): pure, no I/O. Everything that comes from the database or the model
goes in as `Text`, never as a markup string, so `[bold]` in a cell or an answer is printed as it is."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Final

from rich.console import Group
from rich.table import Table
from rich.text import Text

from sherpa_agent.outcomes import Answered, Failed, NeedsClarification, Unanswerable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue
    from rich.console import RenderableType

    from sherpa_agent.outcomes import Draft, Interpretation, InterpretationItem, RunStats, Step

type Refusal = NeedsClarification | Unanswerable | Failed

MAX_TABLE_ROWS: Final = 20  # §5.5: the rest is counted, not shown
MS_PER_SECOND: Final = 1000
NULL_CELL: Final = "NULL"
HEADING_STYLE: Final = "bold"
FOOTER_STYLE: Final = "dim"
FOOTER_SEPARATOR: Final = " | "


def render_progress(step: Step) -> Text:
    calls = ", ".join(f"{call.tool} {call.summary}".strip() for call in step.tool_calls)
    return Text(f"{step.step_id} {step.name}: {calls}" if calls else f"{step.step_id} {step.name}", style=FOOTER_STYLE)


def render_draft(draft: Draft) -> list[RenderableType]:
    return [*_interpretation(draft.interpretation), *_sql("SQL", draft.sql)]


def render_outcome(outcome: Answered | Refusal, trace_url_template: str | None) -> list[RenderableType]:
    match outcome:
        case Answered():
            sections = _answered(outcome)
        case NeedsClarification():
            sections = _clarification(outcome)
        case Unanswerable():
            sections = _unanswerable(outcome)
        case Failed():
            sections = _failed(outcome)
    if outcome.stats is not None:
        sections.append(render_footer(outcome.stats, trace_url_template))
    return sections


def result_table(columns: Sequence[str], rows: Sequence[Sequence[JsonValue]]) -> RenderableType:
    if not rows:
        return Text("(no rows)", style=FOOTER_STYLE)
    table = Table()
    for column in columns:
        table.add_column(Text(column))  # a Text header is not parsed for markup
    for row in rows[:MAX_TABLE_ROWS]:
        table.add_row(*(Text(NULL_CELL if value is None else str(value)) for value in row))
    hidden = len(rows) - MAX_TABLE_ROWS
    if hidden <= 0:
        return table
    return Group(table, Text(f"… {hidden} more", style=FOOTER_STYLE))


def render_footer(stats: RunStats, trace_url_template: str | None) -> Text:
    parts = [f"{stats.latency_ms / MS_PER_SECOND:.1f} s"]
    parts += [f"{use.model} {use.input_tokens} in / {use.output_tokens} out" for use in stats.usage]
    parts.append("cost unknown" if stats.cost_eur is None else f"EUR {stats.cost_eur:.4f}")
    if stats.trace_id is not None:
        parts.append(f"trace: {_trace_link(stats.trace_id, trace_url_template)}")
    return Text(FOOTER_SEPARATOR.join(parts), style=FOOTER_STYLE)


def _answered(outcome: Answered) -> list[RenderableType]:
    sections: list[RenderableType] = [Text(outcome.answer_text), _gap(), result_table(outcome.columns, outcome.rows)]
    if outcome.caveats:
        sections += [_gap(), _heading("Caveats"), *(Text(f"- {caveat}") for caveat in outcome.caveats)]
    sections += _interpretation(outcome.interpretation)
    sections += _sql("SQL", outcome.sql)
    if outcome.sql_revision is not None:
        sections += [_heading("SQL revised"), Text(outcome.sql_revision.reason)]
        sections += _sql("Approved SQL", outcome.sql_revision.previous_sql)
    return sections


def _clarification(outcome: NeedsClarification) -> list[RenderableType]:
    sections: list[RenderableType] = [Text(outcome.question_to_user)]
    sections += [Text(f"- {candidate}") for candidate in outcome.candidate_interpretations]
    if outcome.partial is not None:
        sections += _interpretation(outcome.partial)
    return sections


def _unanswerable(outcome: Unanswerable) -> list[RenderableType]:
    sections: list[RenderableType] = [Text(f"Cannot answer: {outcome.reason}")]
    if outcome.closest_supported is not None:
        sections.append(Text(f"Closest supported: {outcome.closest_supported}"))
    return sections


def _failed(outcome: Failed) -> list[RenderableType]:
    sections: list[RenderableType] = [Text(f"Failed: {outcome.reason}")]
    if outcome.last_error is not None:
        sections.append(Text(f"Last error: {outcome.last_error}"))
    if outcome.last_sql is not None:
        sections += _sql("Last SQL", outcome.last_sql)
    return sections


def _interpretation(interpretation: Interpretation) -> list[RenderableType]:
    if not interpretation.items:
        return []
    sections: list[RenderableType] = [_gap(), _heading("Interpretation")]
    by_step: dict[str, list[InterpretationItem]] = defaultdict(list)  # steps in first-seen order ("s10" after "s9")
    for item in interpretation.items:
        by_step[item.step_id].append(item)
    for step_id, items in by_step.items():
        sections.append(Text(step_id, style=HEADING_STYLE))
        sections += [Text(f"  {item.kind}: {item.value} ({item.grounded_by})") for item in items]
    return sections


def _sql(title: str, sql: str) -> list[RenderableType]:
    return [_gap(), _heading(title), Text(sql)]


def _heading(title: str) -> Text:
    return Text(title, style=HEADING_STYLE)


def _gap() -> Text:
    return Text("")


def _trace_link(trace_id: str, template: str | None) -> str:
    return trace_id if template is None else template.replace("{trace_id}", trace_id)
