"""Result comparison for execution accuracy (agent spec §7.2): pure, no I/O."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Hashable

MAX_MAPPINGS: Final = 10_000  # candidate column mappings tried per case (§7.2)
CENT: Final = Decimal("0.01")
ROW_COUNT_DIFF: Final = "gold has {gold} rows, the agent {agent}"
NO_COLUMN_DIFF: Final = "no agent column matches gold column {name}"
NO_MAPPING_DIFF: Final = "no column mapping matches the gold rows as whole rows"
TOO_MANY_DIFF: Final = "too many candidate column mappings"


class Result(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    diff: str | None = None


class _TooManyMappingsError(Exception):
    pass


def normalize(value: Any) -> Hashable:
    match value:
        case None | bool():  # bool before int: it is an int subclass
            return value
        case int() | float() | Decimal():
            try:
                number = Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)
            except InvalidOperation:  # inf
                return str(value)
            if not number.is_finite():  # nan quantizes quietly
                return str(value)
            return Decimal("0.00") if number.is_zero() else number
        case datetime():
            return (value.astimezone(UTC) if value.tzinfo else value).isoformat()
        case date():
            return value.isoformat()
        case _:
            return str(value)


def compare(gold: Result, agent: Result, *, ordered: bool) -> Comparison:
    gold_rows = [tuple(normalize(cell) for cell in row) for row in gold.rows]
    agent_rows = [tuple(normalize(cell) for cell in row) for row in agent.rows]
    if len(gold_rows) != len(agent_rows):
        return Comparison(passed=False, diff=ROW_COUNT_DIFF.format(gold=len(gold_rows), agent=len(agent_rows)))
    if not gold_rows:  # nothing to project: any agent with enough columns matches
        passed = len(agent.columns) >= len(gold.columns)
        return Comparison(passed=passed, diff=None if passed else NO_MAPPING_DIFF)
    gold_columns = list(zip(*gold_rows, strict=True))
    agent_columns = list(zip(*agent_rows, strict=True))
    candidates = []
    for name, column in zip(gold.columns, gold_columns, strict=True):
        fitting = [i for i, other in enumerate(agent_columns) if Counter(other) == Counter(column)]
        if not fitting:
            return Comparison(passed=False, diff=NO_COLUMN_DIFF.format(name=name))
        candidates.append(fitting)
    try:
        matched = _find_mapping(candidates, agent_rows, gold_rows, ordered=ordered)
    except _TooManyMappingsError:
        return Comparison(passed=False, diff=TOO_MANY_DIFF)
    return Comparison(passed=True) if matched else Comparison(passed=False, diff=NO_MAPPING_DIFF)


def _find_mapping(
    candidates: list[list[int]],
    agent_rows: list[tuple[Hashable, ...]],
    gold_rows: list[tuple[Hashable, ...]],
    *,
    ordered: bool,
) -> bool:
    tried = 0

    def matches(mapping: list[int]) -> bool:
        projected = [tuple(row[i] for i in mapping) for row in agent_rows]
        return projected == gold_rows if ordered else Counter(projected) == Counter(gold_rows)

    def assign(mapping: list[int]) -> bool:
        nonlocal tried
        if tried >= MAX_MAPPINGS:  # every search node counts, so dead ends are bounded too
            raise _TooManyMappingsError
        tried += 1
        if len(mapping) == len(candidates):
            return matches(mapping)
        return any(assign([*mapping, i]) for i in candidates[len(mapping)] if i not in mapping)

    return assign([])
