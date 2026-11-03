"""What Home's data-status strip reads: source freshness from `sources.json` and the last build's failing tests."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from sherpa_ui.lineage.node_status import TEST_OUTCOMES, TestOutcome

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.build.build_status import LastFullBuild

logger = logging.getLogger(__name__)

SOURCES_FILE: Final = "sources.json"
NO_BUILD_NOTE: Final = "no full prod build recorded (run make build-prod)"
BUILD_UNAVAILABLE_NOTE: Final = "build status unavailable"
NO_FRESHNESS_NOTE: Final = "no freshness check recorded (run make build-prod)"
FRESHNESS_UNAVAILABLE_NOTE: Final = "freshness unavailable"
_TEST_TYPES: Final = frozenset({"test", "unit_test"})
_SECONDS_PER_HOUR: Final = 3600
_SOURCE_PREFIX_PARTS: Final = 2  # "source.<project>." comes before the short name
_PROBLEM_STATUSES: Final[dict[str, Literal["warn", "error"]]] = {
    "error": "error",
    "runtime error": "error",
    "warn": "warn",
}
_PROBLEM_ORDER: Final = {"error": 0, "warn": 1}


class FreshnessUnavailable(Exception):
    """`sources.json` exists but can't be read or doesn't have dbt's shape."""


class SourceFreshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: Literal["warn", "error"]
    max_loaded_at: datetime | None
    hours_ago: int | None


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    checked_at: datetime
    problems: tuple[SourceFreshness, ...]


class BuildSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: datetime
    node_count: int
    failing_tests: int


class DataStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of_date: date | None
    build: BuildSummary | None
    build_note: str | None
    freshness: Freshness | None
    freshness_note: str | None


class FreshnessReader:
    """Reads on every call: the file is small, and a rebuild is then picked up without a reload."""

    def __init__(self, target_path: Path) -> None:
        self._path = target_path / SOURCES_FILE

    def read(self) -> Freshness | None:
        if not self._path.is_file():
            return None

        try:
            document = _Document.model_validate(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            raise FreshnessUnavailable(f"cannot read {self._path.name}: {exc}") from exc

        problems = [_to_problem(row) for row in document.results if row.status in _PROBLEM_STATUSES]
        problems.sort(key=lambda p: (_PROBLEM_ORDER[p.status], p.name))
        return Freshness(checked_at=document.metadata.generated_at, problems=tuple(problems))


def failing_test_count(build: LastFullBuild) -> int:
    """Uses Lineage's status mapping, so the strip and the graph always agree."""
    return sum(
        1
        for result in build.results.values()
        if result.resource_type in _TEST_TYPES and TEST_OUTCOMES.get(result.status) == TestOutcome.FAILED
    )


class _Metadata(BaseModel):
    generated_at: datetime


class _Row(BaseModel):
    unique_id: str
    status: str
    max_loaded_at: datetime | None = None
    max_loaded_at_time_ago_in_s: float | None = None


class _Document(BaseModel):
    metadata: _Metadata
    results: list[_Row]


def _to_problem(row: _Row) -> SourceFreshness:
    seconds = row.max_loaded_at_time_ago_in_s
    return SourceFreshness(
        name=row.unique_id.split(".", _SOURCE_PREFIX_PARTS)[-1],
        status=_PROBLEM_STATUSES[row.status],
        max_loaded_at=row.max_loaded_at,
        hours_ago=None if seconds is None else int(seconds // _SECONDS_PER_HOUR),
    )
