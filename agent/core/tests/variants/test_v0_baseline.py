from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import pytest
from fakes import AS_OF_DATE, FakeWarehouse, sql_result
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage
from scripts import QUESTION, SQL, Script, answer_out, clarify, item, plan_draft, tool, unanswerable
from sherpa_agent.guards import NO_SUCH_EXECUTION, NOT_THE_LAST_EXECUTION, NOT_THE_VALIDATED_SQL
from sherpa_agent.outcomes import (
    Answered,
    Correction,
    CorrectionTarget,
    Draft,
    Interpretation,
    InterpretationItem,
    NeedsClarification,
    SqlRevision,
    Step,
    Unanswerable,
    UsageSnapshot,
)
from sherpa_agent.variants import VARIANTS, VariantContext
from sherpa_agent.variants.v0_baseline import V0Baseline
from sherpa_mcp.tools import WarehouseContext

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sherpa_agent.settings import AgentSettings

pytestmark = pytest.mark.anyio


def make_draft(sql: str = SQL, *items: InterpretationItem, corrections: Sequence[Correction] = ()) -> Draft:
    return Draft(
        outcome="draft",
        draft_id="d1",
        question=QUESTION,
        corrections=tuple(corrections),
        interpretation=Interpretation(items=items),
        sql=sql,
        steps=(),
        usage=UsageSnapshot(requests=3, input_tokens=100, output_tokens=50, tool_calls=4),
    )


def time_range_item(value: str) -> InterpretationItem:
    return InterpretationItem(kind="time_range", value=value, step_id="s1", grounded_by="literal")


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def progress() -> list[Step]:
    return []


@pytest.fixture
def make_ctx(settings: AgentSettings, warehouse: FakeWarehouse, progress: list[Step]) -> Callable[..., VariantContext]:
    async def on_progress(step: Step) -> None:
        progress.append(step)

    def build(script: Script, usage: RunUsage | None = None) -> VariantContext:
        return VariantContext(
            settings=settings,
            toolset=warehouse.toolset,
            model_for=lambda _name: script.model,
            usage=usage or RunUsage(),
            on_progress=on_progress,
        )

    return build


def test_the_baseline_is_registered_under_its_name() -> None:
    assert list(VARIANTS) == ["v0_baseline"]
    assert VARIANTS["v0_baseline"].name == "v0_baseline"


class TestPlan:
    async def test_plan_never_calls_run_sql(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        script = Script(
            tool("run_sql", sql=SQL),
            tool("validate_sql", sql=SQL),
            plan_draft(SQL, item("metric", "net_revenue", "default")),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        offered = {tool_def.name for tool_def in script.requests[0][1].function_tools}
        assert "run_sql" not in offered
        assert "validate_sql" in offered
        assert warehouse.calls_of("run_sql") == []
        assert isinstance(outcome, Draft)

    async def test_draft_sql_is_the_last_validated_sql(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(
            tool("validate_sql", sql="select 1"),
            plan_draft("select 2", item("metric", "net_revenue", "default")),
            plan_draft("select 1", item("metric", "net_revenue", "default")),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert NOT_THE_VALIDATED_SQL in script.messages_text(2)
        assert isinstance(outcome, Draft)
        assert outcome.sql == "select 1"

    async def test_draft_carries_the_validated_sql_exactly_as_validated(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(
            tool("validate_sql", sql="select 1;"), plan_draft("select 1", item("metric", "net_revenue", "default"))
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert isinstance(outcome, Draft)
        assert outcome.sql == "select 1;"

    async def test_calls_to_an_unknown_tool_do_not_end_the_run_within_the_spec_retries(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(
            tool("no_such_tool"),
            tool("no_such_tool"),
            tool("run_sql", sql=SQL),
            tool("validate_sql", sql=SQL),
            plan_draft(SQL, item("metric", "net_revenue", "default")),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert isinstance(outcome, Draft)

    async def test_draft_without_any_validation_is_sent_back(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(
            plan_draft("select 1", item("metric", "net_revenue", "default")),
            tool("validate_sql", sql="select 1"),
            plan_draft("select 1", item("metric", "net_revenue", "default")),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert "validate the SQL you return" in script.messages_text(1)
        assert isinstance(outcome, Draft)

    @pytest.mark.parametrize(
        ("grounded_by", "step_id"),
        [
            ("search_values", "s3"),  # the values step
            ("accepted_values", "s2"),  # the schema step
            ("literal", "s4"),  # the sql step
            ("default", "s1"),  # the scope step
            ("correction", "s1"),
        ],
    )
    async def test_item_gets_the_step_of_its_grounding(
        self, make_ctx: Callable[..., VariantContext], grounded_by: str, step_id: str
    ) -> None:
        script = Script(
            tool("get_context"),
            tool("describe_table", table="fct_orders"),
            tool("search_values", table="dim_customers", column="country", term="spain"),
            tool("validate_sql", sql=SQL),
            plan_draft(SQL, item("filter", "country = 'ES'", grounded_by)),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert isinstance(outcome, Draft)
        assert [(s.step_id, s.name) for s in outcome.steps] == [
            ("s1", "scope"),
            ("s2", "schema"),
            ("s3", "values"),
            ("s4", "sql"),
        ]
        assert outcome.interpretation.items[0].step_id == step_id

    async def test_item_without_a_step_of_its_purpose_gets_the_last_step(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(
            tool("get_context"), tool("validate_sql", sql=SQL), plan_draft(SQL, item("filter", "x", "search_values"))
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert isinstance(outcome, Draft)
        assert outcome.interpretation.items[0].step_id == "s2"

    async def test_the_latest_step_of_a_purpose_wins(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(
            tool("search_values", table="t", column="c", term="a"),
            tool("get_context"),
            tool("search_values", table="t", column="c", term="b"),
            tool("validate_sql", sql=SQL),
            plan_draft(SQL, item("filter", "x", "search_values")),
        )

        outcome = await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert isinstance(outcome, Draft)
        assert outcome.interpretation.items[0].step_id == "s3"

    async def test_draft_carries_the_alternatives_the_run_usage_and_the_progress(
        self, make_ctx: Callable[..., VariantContext], progress: list[Step]
    ) -> None:
        alternatives = [
            {"value": "DE", "label": "Germany", "score": 0.4},
            {"value": "AT", "label": "Austria", "score": None},
        ]
        script = Script(
            tool("get_context"),
            tool("validate_sql", sql=SQL),
            plan_draft(SQL, item("filter", "country = 'ES'", "search_values", alternatives)),
        )
        ctx = make_ctx(script)

        outcome = await V0Baseline().plan(QUESTION, [], ctx)

        assert isinstance(outcome, Draft)
        assert [(a.value, a.label, a.score) for a in outcome.interpretation.items[0].alternatives] == [
            ("DE", "Germany", 0.4),
            ("AT", "Austria", None),
        ]
        assert outcome.usage == UsageSnapshot(
            requests=3, input_tokens=ctx.usage.input_tokens, output_tokens=ctx.usage.output_tokens, tool_calls=2
        )
        assert outcome.usage.input_tokens > 0
        assert outcome.stats is None
        assert outcome.question == QUESTION
        assert [s.name for s in progress] == ["scope", "sql"]
        assert [s.name for s in outcome.steps] == ["scope", "sql"]

    async def test_clarify_becomes_needs_clarification(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(tool("get_context"), clarify([item("metric", "revenue", "default")]))

        outcome = await V0Baseline().plan("best customers", [], make_ctx(script))

        assert isinstance(outcome, NeedsClarification)
        assert outcome.question_to_user == "By what?"
        assert outcome.candidate_interpretations == ("revenue", "orders")
        assert outcome.partial is not None
        assert [(i.value, i.step_id) for i in outcome.partial.items] == [("revenue", "s1")]
        assert outcome.stats is None

    async def test_clarify_without_any_step_has_no_partial_interpretation(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(clarify([item("metric", "revenue", "default")]))

        outcome = await V0Baseline().plan("best customers", [], make_ctx(script))

        assert isinstance(outcome, NeedsClarification)
        assert outcome.partial is None

    async def test_clarify_without_items_has_no_partial_interpretation(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(tool("get_context"), clarify([]))

        outcome = await V0Baseline().plan("best customers", [], make_ctx(script))

        assert isinstance(outcome, NeedsClarification)
        assert outcome.partial is None

    async def test_unanswerable_becomes_unanswerable(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(tool("get_context"), unanswerable())

        outcome = await V0Baseline().plan("stock levels?", [], make_ctx(script))

        assert isinstance(outcome, Unanswerable)
        assert outcome.reason == "inventory is not in the warehouse"
        assert outcome.closest_supported == "sales by product"


def scope_with_gap(warehouse: FakeWarehouse) -> None:
    warehouse.set(
        "get_context",
        WarehouseContext(
            as_of_date=dt.date(2024, 6, 30),
            first_order_date=dt.date(2024, 1, 1),
            domains=("sales",),
            covers=("orders",),
            does_not_cover=("inventory levels",),
            notes=("refunds are net of tax",),
        ),
    )


class TestScopeInPrompt:
    async def test_plan_instructions_carry_the_warehouse_scope_without_a_model_call(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        scope_with_gap(warehouse)
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))
        ctx = make_ctx(script)

        outcome = await V0Baseline().plan(QUESTION, [], ctx)

        instructions = script.requests[0][1].instructions or ""
        for text in ("2024-06-30", "inventory levels", "orders", "refunds are net of tax"):
            assert text in instructions
        assert "Call get_context" not in instructions
        assert ctx.usage.tool_calls == 1  # validate_sql only: the scope was not the model's call
        assert isinstance(outcome, Draft)
        assert [s.name for s in outcome.steps] == ["sql"]

    async def test_answer_instructions_carry_the_scope_from_one_get_context_call(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        scope_with_gap(warehouse)
        script = Script(tool("run_sql", sql=SQL), answer_out("As of 2024-06-30 the value is 1"))

        outcome = await V0Baseline().answer(make_draft(), make_ctx(script))

        instructions = script.requests[0][1].instructions or ""
        assert "2024-06-30" in instructions
        assert "inventory levels" in instructions
        assert isinstance(outcome, Answered)  # 2024 / 06 / 30 are known date parts from that same call
        assert len(warehouse.calls_of("get_context")) == 1


class TestCorrections:
    async def test_replace_correction_reaches_the_sql(self, make_ctx: Callable[..., VariantContext]) -> None:
        correction = Correction(
            action="replace", original=CorrectionTarget(kind="filter", value="country = 'DE'"), value="country = 'ES'"
        )
        sql = "select sum(net_revenue) from marts.fct_orders where country = 'ES'"
        script = Script(tool("validate_sql", sql=sql), plan_draft(sql, item("filter", "country = 'ES'", "correction")))

        outcome = await V0Baseline().plan(QUESTION, [correction], make_ctx(script))

        instructions = script.requests[0][1].instructions or ""
        line = "replace filter 'country = 'DE'' with 'country = 'ES'' (record it with grounded_by correction)"
        assert line in instructions
        assert instructions.index(line) < instructions.index("net_revenue")  # ahead of the defaults
        assert isinstance(outcome, Draft)
        assert "country = 'ES'" in outcome.sql
        assert outcome.interpretation.items[0].grounded_by == "correction"
        assert outcome.corrections == (correction,)

    async def test_remove_correction_says_do_not_apply(self, make_ctx: Callable[..., VariantContext]) -> None:
        correction = Correction(action="remove", original=CorrectionTarget(kind="time_range", value="2025"), value=None)
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))

        await V0Baseline().plan(QUESTION, [correction], make_ctx(script))

        assert "do not apply time_range '2025'" in (script.requests[0][1].instructions or "")

    async def test_free_text_correction_is_given_as_written(self, make_ctx: Callable[..., VariantContext]) -> None:
        correction = Correction(action="free_text", original=None, value="only count paid orders")
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))

        await V0Baseline().plan(QUESTION, [correction], make_ctx(script))

        assert "\n- only count paid orders (record it with grounded_by correction)\n" in (
            script.requests[0][1].instructions or ""
        )

    async def test_the_header_says_corrections_override_the_defaults(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        correction = Correction(action="free_text", original=None, value="only count paid orders")
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))

        await V0Baseline().plan(QUESTION, [correction], make_ctx(script))

        instructions = script.requests[0][1].instructions or ""
        assert "they override the defaults below and your own reading of the question" in instructions
        assert instructions.index("Corrections from the user") < instructions.index("Defaults, never ask")

    async def test_the_answer_step_is_given_the_drafts_corrections_ahead_of_the_defaults(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        corrections = [
            Correction(
                action="replace",
                original=CorrectionTarget(kind="metric", value="net_revenue"),
                value="gross_revenue",
            ),
            Correction(action="free_text", original=None, value="only count paid orders"),
        ]
        script = Script(tool("run_sql", sql=SQL), answer_out("The value is 1"))

        await V0Baseline().answer(make_draft(corrections=corrections), make_ctx(script))

        instructions = script.requests[0][1].instructions or ""
        replace_line = "- replace metric 'net_revenue' with 'gross_revenue' (record it with grounded_by correction)"
        assert replace_line in instructions
        assert "\n- only count paid orders (record it with grounded_by correction)\n" in instructions
        assert "override the defaults below" in instructions
        assert instructions.index(replace_line) < instructions.index("Defaults, never ask")

    async def test_the_answer_step_without_corrections_has_no_corrections_block(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("The value is 1"))

        await V0Baseline().answer(make_draft(), make_ctx(script))

        assert "Corrections" not in (script.requests[0][1].instructions or "")

    async def test_no_corrections_means_no_corrections_block(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))

        await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert "Corrections" not in (script.requests[0][1].instructions or "")


class TestAnswer:
    async def test_answered_carries_the_executions_result_not_the_models(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        warehouse.set("run_sql", sql_result("ex-1", rows=((1_200_000,),)))
        draft = make_draft(SQL, time_range_item("2025"))
        script = Script(
            tool("run_sql", sql=SQL),
            answer_out("Revenue was about €1.2M.", rows=[[999]], sql="select 999", truncated=True),
        )

        outcome = await V0Baseline().answer(draft, make_ctx(script))

        assert isinstance(outcome, Answered)
        assert outcome.rows == ((1_200_000,),)
        assert outcome.sql == SQL
        assert outcome.columns == ("n",)
        assert (outcome.returned_rows, outcome.truncated) == (1, False)
        assert outcome.execution_id == "ex-1"
        assert outcome.caveats == ("one caveat",)
        assert outcome.interpretation == draft.interpretation
        assert outcome.sql_revision is None
        assert outcome.stats is None

    async def test_fabricated_number_retries_then_fails(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        warehouse.set("run_sql", sql_result("ex-1", rows=((1_200_000,),)))
        script = Script(tool("run_sql", sql=SQL), answer_out("Revenue was about €1.3M."))

        with pytest.raises(UnexpectedModelBehavior):
            await V0Baseline().answer(make_draft(), make_ctx(script))

        assert "these numbers are not in the result: 1.3M" in script.messages_text()
        assert len(script.requests) == 4  # the run_sql turn, the answer and its two retries

    async def test_a_corrected_answer_passes_on_the_retry(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        warehouse.set("run_sql", sql_result("ex-1", rows=((1_200_000,),)))
        script = Script(tool("run_sql", sql=SQL), answer_out("about €1.3M"), answer_out("about €1.2M"))

        outcome = await V0Baseline().answer(make_draft(), make_ctx(script))

        assert isinstance(outcome, Answered)
        assert outcome.answer_text == "about €1.2M"

    @pytest.mark.parametrize(
        ("first_id", "message"),
        [
            ("ex-1", NOT_THE_LAST_EXECUTION.format(execution_id="ex-1", last_id="ex-2")),
            ("ex-9", NO_SUCH_EXECUTION.format(execution_id="ex-9")),
        ],
    )
    async def test_a_wrong_execution_id_retries(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse, first_id: str, message: str
    ) -> None:
        warehouse.set("run_sql", sql_result("ex-1", rows=((10,),)), sql_result("ex-2", rows=((20,),)))
        script = Script(
            tool("run_sql", sql=SQL),
            tool("run_sql", sql=SQL),
            answer_out("The value is 20", first_id),
            answer_out("The value is 20", "ex-2"),
        )

        outcome = await V0Baseline().answer(make_draft(), make_ctx(script))

        assert message in script.messages_text()
        assert isinstance(outcome, Answered)
        assert (outcome.execution_id, outcome.rows) == ("ex-2", ((20,),))

    @pytest.mark.parametrize(
        ("executed", "revised"),
        [
            (SQL, False),
            (SQL + ";", False),
            ("select  sum(net_revenue)\nfrom marts.fct_orders", False),
            ("select 2", True),
        ],
    )
    async def test_sql_revision_is_set_exactly_when_the_executed_sql_differs(
        self, make_ctx: Callable[..., VariantContext], executed: str, revised: bool
    ) -> None:
        script = Script(tool("run_sql", sql=executed), answer_out("The value is 1"))

        outcome = await V0Baseline().answer(make_draft(), make_ctx(script))

        assert isinstance(outcome, Answered)
        assert outcome.sql == executed
        if revised:
            assert isinstance(outcome.sql_revision, SqlRevision)
            assert outcome.sql_revision.previous_sql == SQL
            assert outcome.sql_revision.reason
        else:
            assert outcome.sql_revision is None

    async def test_as_of_date_comes_from_one_direct_get_context_call(
        self, make_ctx: Callable[..., VariantContext], warehouse: FakeWarehouse
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out(f"As of {AS_OF_DATE.isoformat()} the value is 1"))

        outcome = await V0Baseline().answer(make_draft(), make_ctx(script))

        assert isinstance(outcome, Answered)
        assert warehouse.calls_of("get_context") == [{}]

    @pytest.mark.parametrize(("items", "passes"), [((time_range_item("2019"),), True), ((), False)])
    async def test_time_range_values_of_the_draft_are_known_dates(
        self, make_ctx: Callable[..., VariantContext], items: tuple[InterpretationItem, ...], passes: bool
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("In 2019 the value is 1"))
        draft = make_draft(SQL, *items)

        if passes:
            assert isinstance(await V0Baseline().answer(draft, make_ctx(script)), Answered)
        else:
            with pytest.raises(UnexpectedModelBehavior):
                await V0Baseline().answer(draft, make_ctx(script))

    async def test_the_prompt_hands_over_the_approved_sql_and_interpretation(
        self, make_ctx: Callable[..., VariantContext]
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("The value is 1"))

        await V0Baseline().answer(make_draft(SQL, time_range_item("2019")), make_ctx(script))

        prompt = script.messages_text(0)
        assert QUESTION in prompt
        assert SQL in prompt
        assert "time_range: 2019" in prompt


class TestLimits:
    @pytest.fixture
    def run_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        original = Agent.run

        async def spy(self: Agent[Any, Any], *args: Any, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(Agent, "run", spy)
        return calls

    async def test_plan_runs_with_the_spec_limits_on_the_shared_usage(
        self, make_ctx: Callable[..., VariantContext], run_calls: list[dict[str, Any]]
    ) -> None:
        usage = RunUsage(tool_calls=4)
        script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))
        ctx = make_ctx(script, usage)

        await V0Baseline().plan(QUESTION, [], ctx)

        limits = run_calls[0]["usage_limits"]
        assert (limits.request_limit, limits.total_tokens_limit, limits.tool_calls_limit) == (40, 400_000, 19)
        assert run_calls[0]["usage"] is usage

    async def test_answer_runs_with_the_spec_limits_on_the_shared_usage(
        self, make_ctx: Callable[..., VariantContext], run_calls: list[dict[str, Any]]
    ) -> None:
        usage = RunUsage(requests=3, tool_calls=4)
        script = Script(tool("run_sql", sql=SQL), answer_out("The value is 1"))
        ctx = make_ctx(script, usage)

        await V0Baseline().answer(make_draft(), ctx)

        limits = run_calls[0]["usage_limits"]
        assert (limits.request_limit, limits.total_tokens_limit, limits.tool_calls_limit) == (40, 400_000, 19)
        assert run_calls[0]["usage"] is usage

    async def test_plan_retries_a_bad_output_twice(self, make_ctx: Callable[..., VariantContext]) -> None:
        script = Script(tool("validate_sql", sql="select 1"), plan_draft("select 2", item("metric", "m", "default")))

        with pytest.raises(UnexpectedModelBehavior):
            await V0Baseline().plan(QUESTION, [], make_ctx(script))

        assert len(script.requests) == 4  # validate, then the draft and its two retries

    async def test_no_forced_sampling_or_tool_choice(self, make_ctx: Callable[..., VariantContext]) -> None:
        plan_script = Script(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))
        answer_script = Script(tool("run_sql", sql=SQL), answer_out("The value is 1"))

        await V0Baseline().plan(QUESTION, [], make_ctx(plan_script))
        await V0Baseline().answer(make_draft(), make_ctx(answer_script))

        for script in (plan_script, answer_script):
            for _messages, info in script.requests:
                assert info.model_settings is not None
                assert not {"temperature", "top_p", "tool_choice"} & set(info.model_settings)
                assert info.model_settings["max_tokens"] == 16_000
