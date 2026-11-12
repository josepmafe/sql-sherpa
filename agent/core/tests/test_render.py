from __future__ import annotations

from decimal import Decimal
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from builders import (
    SQL,
    TRACE_ID,
    make_answered,
    make_clarification,
    make_draft,
    make_failed,
    make_item,
    make_stats,
    make_unanswerable,
)
from rich.console import Console
from sherpa_agent.outcomes import Interpretation, ModelUsage, SqlRevision, Step, ToolCall
from sherpa_agent.render import render_draft, render_footer, render_outcome, render_progress, result_table

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType


def text_of(renderables: RenderableType | Sequence[RenderableType]) -> str:
    sink = StringIO()
    console = Console(file=sink, width=120, force_terminal=False, highlight=False)
    for renderable in renderables if isinstance(renderables, list | tuple) else [renderables]:
        console.print(renderable)
    return sink.getvalue()


class TestRenderProgress:
    def test_line_names_the_step_and_its_tool_calls(self) -> None:
        step = Step(
            step_id="s2",
            name="schema",
            tool_calls=(
                ToolCall(tool="describe_table", arguments={}, summary="fct_orders"),
                ToolCall(tool="list_tables", arguments={}, summary=""),
            ),
        )

        line = render_progress(step).plain

        assert line == "s2 schema: describe_table fct_orders, list_tables"

    def test_a_step_without_tool_calls_is_just_named(self) -> None:
        assert render_progress(Step(step_id="s1", name="sql", tool_calls=())).plain == "s1 sql"


class TestResultTable:
    def test_25_rows_show_20_and_5_more(self) -> None:
        rows = tuple((f"row-{i:02d}", i) for i in range(25))

        out = text_of(result_table(("name", "n"), rows))

        assert "row-19" in out
        assert "row-20" not in out
        assert "… 5 more" in out

    def test_20_rows_show_all_without_a_more_line(self) -> None:
        rows = tuple((f"row-{i:02d}", i) for i in range(20))

        out = text_of(result_table(("name", "n"), rows))

        assert "row-19" in out
        assert "more" not in out

    def test_21_rows_show_1_more(self) -> None:
        rows = tuple((f"row-{i:02d}", i) for i in range(21))

        assert "… 1 more" in text_of(result_table(("name", "n"), rows))

    def test_no_rows_say_so(self) -> None:
        assert "no rows" in text_of(result_table(("name",), ()))

    def test_null_is_shown_as_null(self) -> None:
        assert "NULL" in text_of(result_table(("a",), ((None,),)))

    def test_markup_in_a_cell_is_printed_literally(self) -> None:
        out = text_of(result_table(("a",), (("[bold]x[/]",),)))

        assert "[bold]x[/]" in out


class TestRenderOutcome:
    def test_answered_has_its_sections_in_order(self) -> None:
        answered = make_answered(
            caveats=("Only paid orders.",),
            interpretation=Interpretation(
                items=(
                    make_item("net_revenue", "s1"),
                    make_item("Germany", "s2", kind="filter", grounded_by="search_values"),
                )
            ),
        )

        out = text_of(render_outcome(answered, None))

        sections = ("Spain made the most.", "ES", "Only paid orders.", "s1", "Germany", SQL, "4.2 s")
        positions = [out.index(section) for section in sections]
        assert positions == sorted(positions)

    def test_interpretation_is_grouped_by_step(self) -> None:
        interpretation = Interpretation(items=(make_item("aaa", "s1"), make_item("bbb", "s2"), make_item("ccc", "s1")))

        out = text_of(render_outcome(make_answered(interpretation=interpretation), None))

        assert out.count("s1") == 1
        assert out.index("aaa") < out.index("ccc") < out.index("s2") < out.index("bbb")

    def test_a_revised_sql_is_shown_with_its_reason(self) -> None:
        revision = SqlRevision(previous_sql="select 1", reason="the answer step ran different SQL")

        out = text_of(render_outcome(make_answered(sql_revision=revision), None))

        assert "select 1" in out
        assert "the answer step ran different SQL" in out

    def test_markup_in_the_answer_the_sql_and_the_caveats_is_printed_literally(self) -> None:
        answered = make_answered(answer_text="Total [bold]10[/]", sql="select '[/]'", caveats=("[red]careful[/]",))

        out = text_of(render_outcome(answered, None))

        assert "Total [bold]10[/]" in out
        assert "select '[/]'" in out
        assert "[red]careful[/]" in out

    def test_clarification_lists_the_candidates(self) -> None:
        out = text_of(render_outcome(make_clarification(), None))

        assert "Best by what?" in out
        assert "by revenue" in out
        assert "by order count" in out

    def test_unanswerable_gives_the_reason_and_the_closest_supported(self) -> None:
        out = text_of(render_outcome(make_unanswerable(), None))

        assert "inventory is not in the warehouse" in out
        assert "orders by product" in out

    def test_failed_gives_the_reason_the_last_sql_and_the_error(self) -> None:
        failed = make_failed(reason="sql attempts exhausted", last_sql="select oops", last_error="column oops")

        out = text_of(render_outcome(failed, None))

        assert "sql attempts exhausted" in out
        assert "select oops" in out
        assert "column oops" in out

    def test_an_outcome_without_stats_has_no_footer(self) -> None:
        out = text_of(render_outcome(make_unanswerable(stats=None), None))

        assert "trace" not in out
        assert " s\n" not in out


class TestRenderDraft:
    def test_shows_the_interpretation_and_the_sql(self) -> None:
        out = text_of(render_draft(make_draft()))

        assert "net_revenue" in out
        assert SQL in out


class TestRenderFooter:
    def test_template_makes_the_trace_link(self) -> None:
        footer = render_footer(make_stats(), "http://jaeger/trace/{trace_id}").plain

        assert f"trace: http://jaeger/trace/{TRACE_ID}" in footer

    def test_without_a_template_it_is_the_bare_trace_id(self) -> None:
        assert f"trace: {TRACE_ID}" in render_footer(make_stats(), None).plain

    def test_no_trace_id_no_trace_part(self) -> None:
        assert "trace" not in render_footer(make_stats(trace_id=None), None).plain

    def test_latency_tokens_per_model_and_cost(self) -> None:
        stats = make_stats(
            usage=(
                ModelUsage(model="model-a", input_tokens=1200, output_tokens=345),
                ModelUsage(model="model-b", input_tokens=10, output_tokens=2),
            ),
            latency_ms=4200,
            cost_eur=Decimal("0.0123"),
        )

        footer = render_footer(stats, None).plain

        assert footer.startswith("4.2 s")
        assert "model-a 1200 in / 345 out" in footer
        assert "model-b 10 in / 2 out" in footer
        assert "EUR 0.0123" in footer

    def test_an_unpriced_run_says_cost_unknown(self) -> None:
        assert "cost unknown" in render_footer(make_stats(cost_eur=None), None).plain

    @pytest.mark.parametrize("template", ["{trace_id}/x/{trace_id}", "[bold]{trace_id}[/]"])
    def test_template_text_is_not_markup(self, template: str) -> None:
        footer = text_of(render_footer(make_stats(), template))

        assert template.replace("{trace_id}", TRACE_ID) in footer
