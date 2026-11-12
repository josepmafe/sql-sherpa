from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from fakes import AS_OF_DATE, TOOL_NAMES, FakeWarehouse, sql_result, validation
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from sherpa_agent import AgentError
from sherpa_agent.recording import Execution, RecordingToolset, SqlAttemptsExhausted, plan_tools

if TYPE_CHECKING:
    from sherpa_agent.outcomes import Step

pytestmark = pytest.mark.anyio


class Progress:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    async def __call__(self, step: Step) -> None:
        self.steps.append(step)


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def progress() -> Progress:
    return Progress()


@pytest.fixture
def recording(warehouse: FakeWarehouse, progress: Progress) -> RecordingToolset:
    return RecordingToolset(warehouse.toolset, on_progress=progress)


class TestSteps:
    async def test_calls_group_by_purpose_into_numbered_steps(
        self, warehouse: FakeWarehouse, recording: RecordingToolset, progress: Progress
    ) -> None:
        await warehouse.call(recording, "get_context")
        await warehouse.call(recording, "describe_table", table="fct_orders")
        await warehouse.call(recording, "describe_table", table="dim_customers")
        await warehouse.call(recording, "search_values", table="dim_customers", column="country", term="spain")
        await warehouse.call(recording, "validate_sql", sql="select 1")
        awaited_during_calls = len(progress.steps)

        steps = await recording.finish()

        assert [(s.step_id, s.name, [c.tool for c in s.tool_calls]) for s in steps] == [
            ("s1", "scope", ["get_context"]),
            ("s2", "schema", ["describe_table", "describe_table"]),
            ("s3", "values", ["search_values"]),
            ("s4", "sql", ["validate_sql"]),
        ]
        assert awaited_during_calls == 3
        assert [s.step_id for s in progress.steps] == ["s1", "s2", "s3", "s4"]
        assert recording.steps == steps

    async def test_steps_lists_the_open_step_before_finish(
        self, warehouse: FakeWarehouse, recording: RecordingToolset, progress: Progress
    ) -> None:
        await warehouse.call(recording, "get_context")
        await warehouse.call(recording, "describe_table", table="fct_orders")

        assert [(s.step_id, s.name) for s in recording.steps] == [("s1", "scope"), ("s2", "schema")]
        assert [s.step_id for s in progress.steps] == ["s1"]

    async def test_finish_without_calls_returns_no_steps(self, recording: RecordingToolset, progress: Progress) -> None:
        assert await recording.finish() == ()
        assert progress.steps == []

    async def test_finishing_twice_does_not_report_a_step_twice(
        self, warehouse: FakeWarehouse, recording: RecordingToolset, progress: Progress
    ) -> None:
        await warehouse.call(recording, "get_context")
        first = await recording.finish()

        second = await recording.finish()

        assert second == first
        assert len(progress.steps) == 1

    async def test_it_works_without_a_progress_callback(self, warehouse: FakeWarehouse) -> None:
        recording = RecordingToolset(warehouse.toolset, on_progress=None)
        await warehouse.call(recording, "get_context")
        await warehouse.call(recording, "list_tables")

        assert [s.name for s in await recording.finish()] == ["scope"]

    @pytest.mark.parametrize(
        ("tool", "arguments", "step"),
        [
            ("get_context", {}, "scope"),
            ("list_tables", {}, "scope"),
            ("get_metric_definitions", {}, "scope"),
            ("get_examples", {"question": "q"}, "scope"),
            ("describe_table", {"table": "fct_orders"}, "schema"),
            ("search_values", {"table": "t", "column": "c", "term": "x"}, "values"),
            ("validate_sql", {"sql": "select 1"}, "sql"),
            ("run_sql", {"sql": "select 1"}, "sql"),
        ],
    )
    async def test_each_tool_has_its_step(
        self, warehouse: FakeWarehouse, recording: RecordingToolset, tool: str, arguments: dict[str, str], step: str
    ) -> None:
        await warehouse.call(recording, tool, **arguments)

        assert [s.name for s in recording.steps] == [step]


class TestToolCalls:
    async def test_a_call_records_its_arguments_and_a_one_line_summary(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("run_sql", sql_result(rows=((1,), (2,), (3,))))
        warehouse.set("validate_sql", validation("[guard:tables] use marts.<mart>\nsecond line"), validation())

        await warehouse.call(recording, "validate_sql", sql="select x")
        await warehouse.call(recording, "validate_sql", sql="select 1")
        await warehouse.call(recording, "run_sql", sql="select 1")

        assert [(c.arguments, c.summary) for c in recording.steps[0].tool_calls] == [
            ({"sql": "select x"}, "refused: [guard:tables] use marts.<mart>"),
            ({"sql": "select 1"}, "ok"),
            ({"sql": "select 1"}, "ok, 3 rows"),
        ]

    async def test_a_tool_error_is_recorded_as_failed_and_reraised_unchanged(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        boom = RuntimeError("[unavailable] database down\nmore")
        warehouse.set("describe_table", boom)

        with pytest.raises(RuntimeError) as raised:
            await warehouse.call(recording, "describe_table", table="fct_orders")

        assert raised.value is boom
        assert [c.summary for c in recording.steps[0].tool_calls] == ["failed: [unavailable] database down"]

    async def test_a_tool_error_that_is_not_sql_does_not_count_as_an_attempt(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("describe_table", RuntimeError("nope"))

        with pytest.raises(RuntimeError):
            await warehouse.call(recording, "describe_table", table="fct_orders")

        assert recording.sql_attempts == 0
        assert recording.last_error is None


class TestSqlAttemptLimit:
    async def test_three_failing_validations_are_allowed(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"))

        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        assert recording.sql_attempts == 3

    async def test_a_fourth_run_sql_raises_before_it_runs_with_the_last_sql_and_error(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("first error"), validation("second error"), validation("third error"))
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        with pytest.raises(SqlAttemptsExhausted) as raised:
            await warehouse.call(recording, "run_sql", sql="select 99")

        assert raised.value.last_sql == "select 2"
        assert raised.value.last_error == "third error"
        assert warehouse.calls_of("run_sql") == []

    async def test_a_fourth_validation_that_fails_ends_the_run(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"))
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        with pytest.raises(SqlAttemptsExhausted) as raised:
            await warehouse.call(recording, "validate_sql", sql="select 3")

        assert (raised.value.last_sql, raised.value.last_error) == ("select 3", "bad")

    async def test_a_fourth_validation_that_passes_is_allowed(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"), validation("bad"), validation("bad"), validation())
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        await warehouse.call(recording, "validate_sql", sql="select 3")

        assert recording.last_validated_sql == "select 3"
        assert recording.sql_attempts == 3

    async def test_a_passing_validation_is_not_an_attempt(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"), validation("bad"), validation())
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        await warehouse.call(recording, "run_sql", sql="select 2")

        assert recording.sql_attempts == 3
        assert len(recording.executions) == 1

    async def test_the_error_of_an_earlier_failure_is_not_reported_for_a_later_success(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("stale error"))
        await warehouse.call(recording, "validate_sql", sql="select 0")
        await warehouse.call(recording, "run_sql", sql="select 1")
        await warehouse.call(recording, "run_sql", sql="select 2")

        with pytest.raises(SqlAttemptsExhausted) as raised:
            await warehouse.call(recording, "run_sql", sql="select 3")

        assert (raised.value.last_sql, raised.value.last_error) == ("select 2", None)

    async def test_the_limit_leaves_the_record_of_the_refused_call_out(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"))
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        with pytest.raises(SqlAttemptsExhausted):
            await warehouse.call(recording, "run_sql", sql="select 99")

        assert [c.tool for c in recording.steps[0].tool_calls] == ["validate_sql"] * 3

    def test_it_is_an_agent_error(self) -> None:
        assert issubclass(SqlAttemptsExhausted, AgentError)


class TestExecutions:
    async def test_a_successful_run_is_kept_with_columns_and_types(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("run_sql", sql_result("ex-7", rows=((10,), (20,))), sql_result("ex-8", rows=((5,),)))

        await warehouse.call(recording, "run_sql", sql="select a")
        await warehouse.call(recording, "run_sql", sql="select b")

        assert recording.executions == (
            Execution(
                execution_id="ex-7",
                sql="select a",
                columns=("n",),
                column_types=("int8",),
                rows=((10,), (20,)),
                returned_rows=2,
                truncated=False,
            ),
            Execution(
                execution_id="ex-8",
                sql="select b",
                columns=("n",),
                column_types=("int8",),
                rows=((5,),),
                returned_rows=1,
                truncated=False,
            ),
        )

    async def test_a_run_error_is_no_execution_counts_as_an_attempt_and_is_reraised(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        boom = RuntimeError("[timeout] cancelled after 5 s")
        warehouse.set("run_sql", boom)

        with pytest.raises(RuntimeError) as raised:
            await warehouse.call(recording, "run_sql", sql="select slow")

        assert raised.value is boom
        assert recording.executions == ()
        assert recording.sql_attempts == 1
        assert recording.last_error == "[timeout] cancelled after 5 s"


class TestLastValidatedSql:
    async def test_it_follows_the_last_passing_validation_only(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", validation("bad"), validation(), validation("bad again"))
        assert recording.last_validated_sql is None

        await warehouse.call(recording, "validate_sql", sql="select 1")
        assert recording.last_validated_sql is None

        await warehouse.call(recording, "validate_sql", sql="select 2")
        assert recording.last_validated_sql == "select 2"

        await warehouse.call(recording, "validate_sql", sql="select 3")
        assert recording.last_validated_sql == "select 2"


class TestPlanTools:
    async def test_it_hides_run_sql_and_keeps_the_other_seven(self, warehouse: FakeWarehouse) -> None:
        ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())

        tools = await plan_tools(warehouse.toolset).get_tools(ctx)

        assert set(tools) == set(TOOL_NAMES) - {"run_sql"}


async def test_the_fake_serves_the_as_of_date_to_a_direct_call(warehouse: FakeWarehouse) -> None:
    result = await warehouse.call(warehouse.toolset, "get_context")

    assert result["as_of_date"] == AS_OF_DATE.isoformat()


async def test_a_copy_made_by_the_framework_shares_the_record(
    warehouse: FakeWarehouse, recording: RecordingToolset
) -> None:
    copied = recording.visit_and_replace(lambda toolset: toolset.filtered(lambda ctx, tool_def: True))

    await warehouse.call(copied, "run_sql", sql="select 1")

    assert copied is not recording
    assert [e.sql for e in recording.executions] == ["select 1"]
    assert recording.sql_attempts == 1


async def test_parallel_calls_of_different_purposes_keep_their_own_steps_when_progress_yields(
    warehouse: FakeWarehouse,
) -> None:
    async def slow_progress(step: Step) -> None:
        await asyncio.sleep(0.05)

    recording = RecordingToolset(warehouse.toolset, on_progress=slow_progress)
    await warehouse.call(recording, "get_context")

    await asyncio.gather(
        warehouse.call(recording, "describe_table", table="fct_orders"),
        warehouse.call(recording, "search_values", table="dim_customers", column="country", term="spain"),
    )
    steps = await recording.finish()

    assert [(s.step_id, s.name, [c.tool for c in s.tool_calls]) for s in steps] == [
        ("s1", "scope", ["get_context"]),
        ("s2", "schema", ["describe_table"]),
        ("s3", "values", ["search_values"]),
    ]


async def test_a_recorder_without_a_progress_callback_works(warehouse: FakeWarehouse) -> None:
    recording = RecordingToolset(warehouse.toolset)
    await warehouse.call(recording, "get_context")
    await warehouse.call(recording, "validate_sql", sql="select 1")

    assert [s.name for s in await recording.finish()] == ["scope", "sql"]


class TestSqlToolErrors:
    async def test_a_validate_error_counts_as_an_attempt(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        warehouse.set("validate_sql", RuntimeError("[unavailable] down"))

        with pytest.raises(RuntimeError):
            await warehouse.call(recording, "validate_sql", sql="select 1")

        assert (recording.sql_attempts, recording.last_error) == (1, "[unavailable] down")

    async def test_a_fourth_attempt_that_errors_raises_exhausted_chained_from_the_error(
        self, warehouse: FakeWarehouse, recording: RecordingToolset
    ) -> None:
        boom = RuntimeError("[timeout] slow")
        warehouse.set("validate_sql", validation("bad"), validation("bad"), validation("bad"), boom)
        for n in range(3):
            await warehouse.call(recording, "validate_sql", sql=f"select {n}")

        with pytest.raises(SqlAttemptsExhausted) as raised:
            await warehouse.call(recording, "validate_sql", sql="select 3")

        assert raised.value.__cause__ is boom
        assert (raised.value.last_sql, raised.value.last_error) == ("select 3", "[timeout] slow")
