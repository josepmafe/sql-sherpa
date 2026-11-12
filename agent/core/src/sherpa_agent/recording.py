"""The record every guard trusts (agent spec §5.2, §5.3): tool calls in steps, and the SQL-attempt limit."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Final, Self

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.toolsets import WrapperToolset

from sherpa_agent import AgentError
from sherpa_agent.outcomes import Step, StepName, ToolCall

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic_ai import RunContext
    from pydantic_ai.toolsets import AbstractToolset
    from pydantic_ai.toolsets.abstract import ToolsetTool

MAX_SQL_ATTEMPTS: Final = 3  # §5.3: the 4th attempt ends the run
RUN_SQL: Final = "run_sql"
VALIDATE_SQL: Final = "validate_sql"
STEP_OF_TOOL: Final[dict[str, StepName]] = {
    "get_context": "scope",
    "list_tables": "scope",
    "get_metric_definitions": "scope",
    "get_examples": "scope",
    "describe_table": "schema",
    "search_values": "values",
    VALIDATE_SQL: "sql",
    RUN_SQL: "sql",
}


class Execution(BaseModel):
    """A successful `run_sql`: what the answer's rows come from."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    sql: str
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    returned_rows: int
    truncated: bool


class SqlAttemptsExhausted(AgentError):
    """The 4th SQL attempt: a run-ending error that carries what the `Failed` outcome reports."""

    def __init__(self, last_sql: str | None, last_error: str | None) -> None:
        super().__init__(f"more than {MAX_SQL_ATTEMPTS} SQL attempts; the last error was: {last_error}")
        self.last_sql = last_sql
        self.last_error = last_error


class RecordingToolset(WrapperToolset[Any]):
    """Wraps the MCP toolset for one run. The record lives in `self._record`, which a copy made by PydanticAI's
    `for_run` shares with this instance, so it survives the framework re-wrapping the toolset."""

    def __init__(
        self, wrapped: AbstractToolset[Any], *, on_progress: Callable[[Step], Awaitable[None]] | None = None
    ) -> None:
        super().__init__(wrapped)
        self._on_progress = on_progress
        self._record = _Record()

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        sql = tool_args.get("sql")
        if name == RUN_SQL:
            if self._record.attempts >= MAX_SQL_ATTEMPTS:
                raise self._exhausted()
            self._record.attempts += 1  # counted before the await, so concurrent calls cannot slip past the limit
            self._record.last_sql, self._record.last_error = sql, None
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except Exception as error:
            message = str(error)
            await self._add_call(name, tool_args, f"failed: {_first_line(message)}")
            if name in (RUN_SQL, VALIDATE_SQL):
                self._count_failed_attempt(name, sql, message)
                self._raise_when_exhausted(error)
            raise
        await self._add_call(name, tool_args, self._after_success(name, sql, result))
        if name == VALIDATE_SQL and not _mapping(name, result)["ok"]:
            self._raise_when_exhausted(None)
        return result

    @property
    def steps(self) -> tuple[Step, ...]:
        """The completed steps and the open one."""
        return tuple(self._record.completed) + ((self._record.open_step(),) if self._record.open_calls else ())

    @property
    def executions(self) -> tuple[Execution, ...]:
        return tuple(self._record.executions)

    @property
    def last_validated_sql(self) -> str | None:
        return self._record.last_validated_sql

    @property
    def sql_attempts(self) -> int:
        return self._record.attempts

    @property
    def last_error(self) -> str | None:
        """The error of the last SQL attempt; None when that attempt succeeded."""
        return self._record.last_error

    async def finish(self) -> tuple[Step, ...]:
        """Complete the open step and return all steps."""
        await self._complete_open_step()
        return self.steps

    async def for_run(self, ctx: RunContext[Any]) -> AbstractToolset[Any]:
        return self._rewrapped(await self.wrapped.for_run(ctx))

    async def for_run_step(self, ctx: RunContext[Any]) -> AbstractToolset[Any]:
        return self._rewrapped(await self.wrapped.for_run_step(ctx))

    def visit_and_replace(
        self, visitor: Callable[[AbstractToolset[Any]], AbstractToolset[Any]]
    ) -> AbstractToolset[Any]:
        return self._rewrapped(self.wrapped.visit_and_replace(visitor))

    def _rewrapped(self, wrapped: AbstractToolset[Any]) -> Self:
        if wrapped is self.wrapped:
            return self
        clone = copy.copy(self)
        clone.wrapped = wrapped
        return clone

    def _after_success(self, name: str, sql: Any, result: Any) -> str:
        match name:
            case "run_sql":
                execution = Execution.model_validate({**_mapping(name, result), "sql": sql})
                self._record.executions.append(execution)
                return f"ok, {execution.returned_rows} rows"
            case "validate_sql":
                validation = _mapping(name, result)
                if validation["ok"]:
                    self._record.last_validated_sql = sql
                    return "ok"
                errors = validation.get("errors") or [""]
                self._count_failed_attempt(name, sql, str(errors[0]))
                return f"refused: {_first_line(str(errors[0]))}"
            case _:
                return "ok"

    def _count_failed_attempt(self, name: str, sql: Any, message: str) -> None:
        if name == VALIDATE_SQL:
            self._record.attempts += 1
        self._record.last_sql, self._record.last_error = sql, message

    def _raise_when_exhausted(self, cause: BaseException | None) -> None:
        if self._record.attempts > MAX_SQL_ATTEMPTS:
            raise self._exhausted() from cause

    def _exhausted(self) -> SqlAttemptsExhausted:
        return SqlAttemptsExhausted(self._record.last_sql, self._record.last_error)

    async def _add_call(self, name: str, arguments: dict[str, Any], summary: str) -> None:
        # All state changes happen before the first await: on_progress may yield to a parallel tool call.
        step_name = STEP_OF_TOOL[name]
        completed = self._record.complete_open_step() if self._record.open_name != step_name else None
        self._record.open_name = step_name
        self._record.open_calls.append(ToolCall(tool=name, arguments=arguments, summary=summary))
        await self._report(completed)

    async def _complete_open_step(self) -> None:
        await self._report(self._record.complete_open_step())

    async def _report(self, step: Step | None) -> None:
        if step is not None and self._on_progress is not None:
            await self._on_progress(step)


def plan_tools(toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
    """The toolset `plan` runs with: `run_sql` is not offered at all (§5.1)."""
    return toolset.filtered(lambda ctx, tool_def: tool_def.name != RUN_SQL)


class _Record:
    """The mutable state of one run."""

    def __init__(self) -> None:
        self.completed: list[Step] = []
        self.open_name: StepName | None = None
        self.open_calls: list[ToolCall] = []
        self.executions: list[Execution] = []
        self.last_validated_sql: str | None = None
        self.attempts = 0
        self.last_sql: str | None = None
        self.last_error: str | None = None

    def open_step(self) -> Step:
        assert self.open_name is not None
        return Step(step_id=f"s{len(self.completed) + 1}", name=self.open_name, tool_calls=tuple(self.open_calls))

    def complete_open_step(self) -> Step | None:
        if not self.open_calls:
            return None
        step = self.open_step()
        self.completed.append(step)
        self.open_calls = []
        self.open_name = None
        return step


def _mapping(tool: str, result: Any) -> Mapping[str, Any]:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    raise AgentError(f"{tool} returned {type(result).__name__}, not a structured result")


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0]
