"""Run records, prices and the markdown report (agent spec §7.2, §7.3): pure except `previous_run` and `write_run`."""

from __future__ import annotations

import itertools
import math
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import yaml
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from sherpa_evals.answerers import ModelUsage

TOKENS_PER_MTOK: Final = Decimal(1_000_000)
P50_TARGET_S: Final = 20  # §7.3
P95_TARGET_S: Final = 60  # §7.3
TIMESTAMP_FORMAT: Final = "%Y%m%dT%H%M%SZ"
NO_PREVIOUS: Final = "No earlier run of {variant} with gold_version {gold} and dataset_version {dataset}."


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_eur_per_mtok: Decimal
    output_eur_per_mtok: Decimal


class Prices(BaseModel):
    model_config = ConfigDict(frozen=True)

    models: dict[str, ModelPrice]

    @classmethod
    def load(cls, path: Path) -> Prices:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def cost_eur(self, usage: Sequence[ModelUsage]) -> Decimal | None:
        """`None` when a model has no price: an unknown cost is not zero."""
        if self.unpriced(usage):
            return None
        total = Decimal(0)
        for item in usage:
            price = self.models[item.model]
            total += item.input_tokens * price.input_eur_per_mtok + item.output_tokens * price.output_eur_per_mtok
        return total / TOKENS_PER_MTOK

    def unpriced(self, usage: Sequence[ModelUsage]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.model for item in usage if item.model not in self.models))


class CaseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: str
    repeat: int
    expected_outcome: str
    outcome: str
    passed: bool
    diff: str | None
    agent_sql: str | None
    latency_ms: int
    cost_eur: Decimal | None
    unpriced_models: tuple[str, ...]
    tool_calls: int
    sql_attempts: int
    schema_recall: bool | None
    trace_id: str | None


class RunMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant: str
    gold_version: int
    dataset_version: int
    started_at: datetime
    repeat: int


def render_report(
    meta: RunMeta,
    records: Sequence[CaseRecord],
    previous: Sequence[CaseRecord] | None,
    previous_name: str | None,
    compared_variant: str,
) -> str:
    """`compared_variant` is the variant whose earlier run was looked up (the default variant, §7.2)."""
    case_count = len({r.case_id for r in records})
    answered = [r for r in records if r.expected_outcome == "answered"]
    lines = [
        f"# Eval report: {meta.variant}",
        "",
        f"gold_version {meta.gold_version} | dataset_version {meta.dataset_version} | "
        f"{meta.started_at:%Y-%m-%d} | repeats {meta.repeat} | cases {case_count}",
        "",
        f"Execution accuracy (EX): {_share(_passed(answered), len(answered))}",
        "",
        "| category | EX |",
        "| --- | --- |",
        *_ex_rows(answered, lambda r: r.category),
        *(_ex_rows(answered, lambda r: f"repeat {r.repeat}") if meta.repeat > 1 else []),
        "",
        f"Outcome-type accuracy: {_share(sum(r.outcome == r.expected_outcome for r in records), len(records))}",
        "",
        *_latency_lines(records),
        _cost_line(records),
        "",
        f"Effort: mean tool calls {_mean([r.tool_calls for r in records]):.1f}, "
        f"mean SQL attempts {_mean([r.sql_attempts for r in records]):.1f}",
        "",
        *_recall_lines(records),
        "",
        *_failing_lines(records),
        "",
        *_diff_lines(meta, records, previous, previous_name, compared_variant),
    ]
    return "\n".join(lines) + "\n"


def previous_run(
    runs_dir: Path, variant: str, gold_version: int, dataset_version: int
) -> tuple[str, list[CaseRecord]] | None:
    """The newest run of `variant` made against the same gold set and dataset."""
    if not runs_dir.exists():
        return None

    for directory in sorted(runs_dir.iterdir(), reverse=True):
        meta_path = directory / "meta.json"
        # cases.jsonl is not committed, so a fresh clone has runs with only meta.json and report.md
        if (
            not directory.name.endswith(f"-{variant}")
            or not meta_path.exists()
            or not (directory / "cases.jsonl").exists()
        ):
            continue

        meta = RunMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        if (meta.variant, meta.gold_version, meta.dataset_version) == (variant, gold_version, dataset_version):
            lines = (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()
            return directory.name, [CaseRecord.model_validate_json(line) for line in lines if line]
    return None


def write_run(runs_dir: Path, meta: RunMeta, records: Sequence[CaseRecord], report: str) -> Path:
    stamp = meta.started_at.strftime(TIMESTAMP_FORMAT)
    directory = runs_dir / f"{stamp}-{meta.variant}"
    for number in itertools.count(1):  # a second run in the same second gets `.1`, which sorts after the first
        try:
            directory.mkdir(parents=True)
            break
        except FileExistsError:
            directory = runs_dir / f"{stamp}.{number}-{meta.variant}"
    (directory / "meta.json").write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (directory / "cases.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
    (directory / "report.md").write_text(report, encoding="utf-8")
    return directory


def _passed(records: Sequence[CaseRecord]) -> int:
    return sum(r.passed for r in records)


def _share(part: int, total: int) -> str:
    return f"{100 * part / total:.1f}% ({part}/{total})" if total else "n/a"


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ex_rows(answered: Sequence[CaseRecord], key: Callable[[CaseRecord], str]) -> list[str]:
    groups: dict[str, list[CaseRecord]] = {}
    for record in answered:
        groups.setdefault(key(record), []).append(record)
    return [f"| {name} | {_share(_passed(group), len(group))} |" for name, group in sorted(groups.items())]


def _percentile_s(values: Sequence[int], fraction: float) -> float:
    ranked = sorted(values)
    return ranked[max(math.ceil(fraction * len(ranked)) - 1, 0)] / 1000


def _latency_lines(records: Sequence[CaseRecord]) -> list[str]:
    latencies = [r.latency_ms for r in records]
    if not latencies:
        return ["Latency: n/a"]
    return [
        f"Latency: p50 {_percentile_s(latencies, 0.5):.1f} s (target {P50_TARGET_S} s), "
        f"p95 {_percentile_s(latencies, 0.95):.1f} s (target {P95_TARGET_S} s)"
    ]


def _cost_line(records: Sequence[CaseRecord]) -> str:
    unpriced = tuple(dict.fromkeys(model for r in records for model in r.unpriced_models))
    costs = [r.cost_eur for r in records if r.cost_eur is not None]
    if unpriced or len(costs) < len(records):
        return f"Cost: cost unknown (no price for {', '.join(unpriced)})"
    total = sum(costs, Decimal(0))
    mean = total / len(costs) if costs else Decimal(0)
    return f"Cost: mean EUR {mean:.4f} per case, total EUR {total:.4f}"


def _recall_lines(records: Sequence[CaseRecord]) -> list[str]:
    measured = [r.schema_recall for r in records if r.schema_recall is not None]
    return [f"Schema recall: {_share(sum(measured), len(measured))}"]


def _failing_lines(records: Sequence[CaseRecord]) -> list[str]:
    failing = [r for r in records if not r.passed]
    if not failing:
        return ["No failing cases."]
    rows = (f"| {r.case_id} | {r.outcome} | {r.diff or '-'} | {r.trace_id or '-'} |" for r in failing)
    return ["Failing cases:", "", "| case | outcome | diff | trace |", "| --- | --- | --- | --- |", *rows]


def _diff_lines(
    meta: RunMeta,
    records: Sequence[CaseRecord],
    previous: Sequence[CaseRecord] | None,
    previous_name: str | None,
    compared_variant: str,
) -> list[str]:
    if previous is None:
        return [NO_PREVIOUS.format(variant=compared_variant, gold=meta.gold_version, dataset=meta.dataset_version)]

    before = _case_results(previous)
    now = _case_results(records)
    shared = before.keys() & now.keys()
    passing = sorted(i for i in shared if now[i] and not before[i])
    failing = sorted(i for i in shared if before[i] and not now[i])
    ex_now = _ex([r for r in records if r.case_id in shared])  # a --case run is not weighed against a full one
    ex_before = _ex([r for r in previous if r.case_id in shared])
    plural = "" if len(shared) == 1 else "s"
    return [
        f"Against {previous_name}:",
        "",
        f"EX change: {ex_now - ex_before:+.1f} points (over the {len(shared)} shared case{plural})",
        f"Newly passing: {', '.join(passing) or '-'}",
        f"Newly failing: {', '.join(failing) or '-'}",
    ]


def _case_results(records: Sequence[CaseRecord]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for record in records:
        results[record.case_id] = results.get(record.case_id, True) and record.passed
    return results


def _ex(records: Sequence[CaseRecord]) -> float:
    answered = [r for r in records if r.expected_outcome == "answered"]
    return 100 * _passed(answered) / len(answered) if answered else 0.0
