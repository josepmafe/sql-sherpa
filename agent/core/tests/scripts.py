"""Scripted `FunctionModel` turns shared by the variant and service tests."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

QUESTION = "How much revenue did Spain make?"
SQL = "select sum(net_revenue) from marts.fct_orders"


class Script:
    """A `FunctionModel` function: one turn per model request, the last turn repeating. Records what it was sent."""

    def __init__(self, *turns: list[Any]) -> None:
        self._turns = list(turns)
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append((list(messages), info))
        turn = self._turns.pop(0) if len(self._turns) > 1 else self._turns[0]
        return ModelResponse(parts=turn)

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self)

    def messages_text(self, request: int = -1) -> str:
        return "\n".join(str(message) for message in self.requests[request][0])


def tool(name: str, **arguments: Any) -> list[Any]:
    return [ToolCallPart(tool_name=name, args=arguments)]


def plan_draft(sql: str, *items: dict[str, Any]) -> list[Any]:
    data = {"sql": sql, "items": list(items)}
    return [TextPart(json.dumps({"result": {"kind": "PlanDraftOut", "data": data}}))]


def item(kind: str, value: str, grounded_by: str, alternatives: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"kind": kind, "value": value, "grounded_by": grounded_by, "alternatives": alternatives or []}


def clarify(partial_items: list[dict[str, Any]]) -> list[Any]:
    data = {
        "question_to_user": "By what?",
        "candidate_interpretations": ["revenue", "orders"],
        "partial_items": partial_items,
    }
    return [TextPart(json.dumps({"result": {"kind": "ClarifyOut", "data": data}}))]


def unanswerable() -> list[Any]:
    data = {"reason": "inventory is not in the warehouse", "closest_supported": "sales by product"}
    return [TextPart(json.dumps({"result": {"kind": "UnanswerableOut", "data": data}}))]


def answer_out(text: str, execution_id: str = "ex-1", **extra: Any) -> list[Any]:
    payload = {"answer_text": text, "caveats": ["one caveat"], "execution_id": execution_id, **extra}
    return [TextPart(json.dumps(payload))]
