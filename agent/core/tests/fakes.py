"""A stand-in for the MCP toolset: the eight real tool names, canned results set per test (agent spec §4.1)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from mcp.shared.exceptions import MCPError
from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.usage import RunUsage
from sherpa_mcp.tools import SqlResult, Validation, WarehouseContext

TOOL_NAMES = (
    "get_context",
    "list_tables",
    "describe_table",
    "search_values",
    "get_metric_definitions",
    "get_examples",
    "validate_sql",
    "run_sql",
)
AS_OF_DATE = dt.date(2025, 12, 31)


class FakeWarehouse:
    """`toolset` offers the eight tools. `set(tool, *results)` queues what the tool returns, one result per call and
    the last one for every call after; an exception in the queue is raised. `calls` records every call that ran."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._results: dict[str, list[Any]] = {name: [_default_result(name)] for name in TOOL_NAMES}
        self.toolset: FunctionToolset[None] = FunctionToolset()
        for name in TOOL_NAMES:
            self.toolset.add_function(getattr(self, name), name=name, takes_ctx=False)

    def set(self, tool: str, *results: Any) -> None:
        self._results[tool] = list(results)

    def calls_of(self, tool: str) -> list[dict[str, Any]]:
        return [arguments for name, arguments in self.calls if name == tool]

    async def call(self, toolset: AbstractToolset[None], name: str, **arguments: Any) -> Any:
        """Call a tool through `toolset` (a wrapper of `self.toolset`) the way an agent run does."""
        ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        tool = (await toolset.get_tools(ctx))[name]
        return await toolset.call_tool(name, arguments, ctx, tool)

    def get_context(self) -> Any:
        return self._next("get_context", {})

    def list_tables(self) -> Any:
        return self._next("list_tables", {})

    def describe_table(self, table: str) -> Any:
        return self._next("describe_table", {"table": table})

    def search_values(self, table: str, column: str, term: str) -> Any:
        return self._next("search_values", {"table": table, "column": column, "term": term})

    def get_metric_definitions(self) -> Any:
        return self._next("get_metric_definitions", {})

    def get_examples(self, question: str) -> Any:
        return self._next("get_examples", {"question": question})

    def validate_sql(self, sql: str) -> Any:
        return self._next("validate_sql", {"sql": sql})

    def run_sql(self, sql: str) -> Any:
        return self._next("run_sql", {"sql": sql})

    def _next(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        queue = self._results[name]
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, BaseException):
            raise result
        return result.model_dump(mode="json") if isinstance(result, BaseModel) else result


def validation(*errors: str) -> Validation:
    return Validation(ok=not errors, errors=errors, tables=("marts.fct_orders",))


def sql_result(execution_id: str = "ex-1", rows: tuple[tuple[object, ...], ...] = ((1,),)) -> SqlResult:
    return SqlResult(
        execution_id=execution_id,
        columns=("n",),
        column_types=("int8",),
        rows=rows,
        returned_rows=len(rows),
        truncated=False,
        elapsed_ms=3,
    )


def _default_result(name: str) -> Any:
    match name:
        case "get_context":
            return WarehouseContext(
                as_of_date=AS_OF_DATE,
                first_order_date=dt.date(2025, 1, 1),
                domains=("sales",),
                covers=(),
                does_not_cover=(),
                notes=(),
            )
        case "list_tables":
            return {"tables": []}
        case "describe_table":
            return {"name": "fct_orders", "columns": []}
        case "search_values":
            return {"matches": [], "hint": None}
        case "get_metric_definitions":
            return {"metrics": []}
        case "get_examples":
            return {"examples": []}
        case "validate_sql":
            return validation()
        case "run_sql":
            return sql_result()
        case _:
            raise KeyError(name)


def mcp_failure(code: int, message: str = "the server went away") -> ModelRetry:
    """What `MCPToolset` raises when the server's session dies under a call: a `ModelRetry` caused by an `MCPError`."""
    retry = ModelRetry(message)
    retry.__cause__ = MCPError(code, message)
    return retry
