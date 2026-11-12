"""Builders for outcomes in the CLI and render tests: pass only what the test asserts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sherpa_agent.outcomes import (
    Answered,
    Draft,
    Failed,
    Interpretation,
    InterpretationItem,
    ModelUsage,
    NeedsClarification,
    RunStats,
    Step,
    ToolCall,
    Unanswerable,
    UsageSnapshot,
)

SQL = "select country, sum(net_revenue) from marts.fct_orders group by 1"
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


def make_stats(**overrides: Any) -> RunStats:
    fields: dict[str, Any] = {
        "variant": "v0_baseline",
        "usage": (ModelUsage(model="model-a", input_tokens=1200, output_tokens=345),),
        "latency_ms": 4200,
        "cost_eur": Decimal("0.0123"),
        "tool_calls": 3,
        "sql_attempts": 1,
        "trace_id": TRACE_ID,
    }
    return RunStats(**{**fields, **overrides})


def make_item(value: str = "net_revenue", step_id: str = "s1", **overrides: Any) -> InterpretationItem:
    fields: dict[str, Any] = {"kind": "metric", "value": value, "step_id": step_id, "grounded_by": "default"}
    return InterpretationItem(**{**fields, **overrides})


def make_draft(**overrides: Any) -> Draft:
    scope = Step(step_id="s1", name="scope", tool_calls=(ToolCall(tool="get_context", arguments={}, summary=""),))
    fields: dict[str, Any] = {
        "outcome": "draft",
        "draft_id": "d-1",
        "question": "revenue by country",
        "corrections": (),
        "interpretation": Interpretation(items=(make_item(),)),
        "sql": SQL,
        "steps": (scope,),
        "usage": UsageSnapshot(requests=1, input_tokens=10, output_tokens=5, tool_calls=1),
        "stats": make_stats(),
    }
    return Draft(**{**fields, **overrides})


def make_answered(**overrides: Any) -> Answered:
    fields: dict[str, Any] = {
        "outcome": "answered",
        "answer_text": "Spain made the most.",
        "interpretation": Interpretation(items=(make_item(),)),
        "execution_id": "ex-1",
        "sql": SQL,
        "columns": ("country", "net_revenue"),
        "rows": (("ES", 10), ("DE", 5)),
        "returned_rows": 2,
        "truncated": False,
        "caveats": (),
        "sql_revision": None,
        "stats": make_stats(),
    }
    return Answered(**{**fields, **overrides})


def make_clarification(**overrides: Any) -> NeedsClarification:
    fields: dict[str, Any] = {
        "outcome": "needs_clarification",
        "question_to_user": "Best by what?",
        "candidate_interpretations": ("by revenue", "by order count"),
        "partial": None,
        "stats": make_stats(),
    }
    return NeedsClarification(**{**fields, **overrides})


def make_unanswerable(**overrides: Any) -> Unanswerable:
    fields: dict[str, Any] = {
        "outcome": "unanswerable",
        "reason": "inventory is not in the warehouse",
        "closest_supported": "orders by product",
        "stats": make_stats(),
    }
    return Unanswerable(**{**fields, **overrides})


def make_failed(**overrides: Any) -> Failed:
    fields: dict[str, Any] = {
        "outcome": "failed",
        "reason": "budget exceeded",
        "last_sql": None,
        "last_error": None,
        "stats": make_stats(),
    }
    return Failed(**{**fields, **overrides})
