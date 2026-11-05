from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sherpa_evals.answerers import ModelUsage
from sherpa_evals.report import CaseRecord, Prices, RunMeta, previous_run, render_report, write_run
from sherpa_evals.settings import PRICES_PATH

if TYPE_CHECKING:
    from pathlib import Path

META = RunMeta(
    variant="oracle",
    gold_version=3,
    dataset_version=2,
    started_at=datetime(2026, 11, 5, 9, 30, tzinfo=UTC),
    repeat=1,
)
PRICES = Prices.model_validate({"models": {"m-priced": {"input_eur_per_mtok": "2", "output_eur_per_mtok": "10"}}})


def record(case_id: str = "values-001", **overrides: Any) -> CaseRecord:
    fields: dict[str, Any] = {
        "case_id": case_id,
        "category": case_id.rsplit("-", 1)[0],
        "repeat": 1,
        "expected_outcome": "answered",
        "outcome": "answered",
        "passed": True,
        "diff": None,
        "agent_sql": "select 1",
        "latency_ms": 1000,
        "cost_eur": Decimal("0.01"),
        "unpriced_models": (),
        "tool_calls": 2,
        "sql_attempts": 1,
        "schema_recall": True,
        "trace_id": None,
    }
    fields.update(overrides)
    return CaseRecord(**fields)


def render(records: list[CaseRecord], previous: list[CaseRecord] | None = None, meta: RunMeta = META) -> str:
    return render_report(meta, records, previous, "20261101T000000Z-oracle" if previous is not None else None, "oracle")


class TestPrices:
    def test_cost_is_tokens_times_price_per_million(self) -> None:
        usage = (ModelUsage(model="m-priced", input_tokens=500_000, output_tokens=100_000),)

        assert PRICES.cost_eur(usage) == Decimal("2.0")  # 0.5 * 2 + 0.1 * 10

    def test_costs_of_several_models_add_up(self) -> None:
        usage = (
            ModelUsage(model="m-priced", input_tokens=1_000_000, output_tokens=0),
            ModelUsage(model="m-priced", input_tokens=0, output_tokens=1_000_000),
        )

        assert PRICES.cost_eur(usage) == Decimal(12)

    def test_a_model_without_a_price_makes_the_cost_none(self) -> None:
        usage = (
            ModelUsage(model="m-priced", input_tokens=1, output_tokens=1),
            ModelUsage(model="m-unknown", input_tokens=1, output_tokens=1),
        )

        assert PRICES.cost_eur(usage) is None
        assert PRICES.unpriced(usage) == ("m-unknown",)

    def test_no_usage_costs_nothing(self) -> None:
        assert PRICES.cost_eur(()) == Decimal(0)

    def test_the_shipped_price_file_prices_the_three_models(self) -> None:
        prices = Prices.load(PRICES_PATH)

        assert set(prices.models) == {
            "eu.anthropic.claude-sonnet-5-5",
            "eu.anthropic.claude-haiku-5-5",
            "eu.anthropic.claude-opus-5-5",
        }

    def test_a_price_file_missing_a_field_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "prices.yaml"
        path.write_text("models:\n  m:\n    input_eur_per_mtok: 1\n", encoding="utf-8")

        with pytest.raises(ValueError, match="output_eur_per_mtok"):
            Prices.load(path)


class TestRenderReport:
    def test_ex_counts_only_answered_cases_and_outcome_accuracy_all_cases(self) -> None:
        records = [
            record("values-001"),
            record("values-002"),
            record("metrics-001", passed=False, diff="row count"),
            record("unanswerable-001", expected_outcome="unanswerable", outcome="unanswerable", agent_sql=None),
        ]

        report = render(records)

        assert "Execution accuracy (EX): 66.7% (2/3)" in report
        assert "| values | 100.0% (2/2) |" in report
        assert "| metrics | 0.0% (0/1) |" in report
        assert "Outcome-type accuracy: 100.0% (4/4)" in report

    def test_outcome_accuracy_counts_a_wrong_outcome(self) -> None:
        records = [record(), record("values-002", outcome="needs_clarification", passed=False)]

        assert "Outcome-type accuracy: 50.0% (1/2)" in render(records)

    def test_latency_percentiles_use_nearest_rank(self) -> None:
        records = [record(f"values-{n:03d}", latency_ms=n * 1000) for n in range(1, 21)]

        report = render(records)

        assert "p50 10.0 s (target 20 s)" in report
        assert "p95 19.0 s (target 60 s)" in report

    def test_cost_is_mean_and_total(self) -> None:
        records = [record("values-001", cost_eur=Decimal("0.10")), record("values-002", cost_eur=Decimal("0.30"))]

        report = render(records)

        assert "mean EUR 0.2000 per case, total EUR 0.4000" in report

    def test_unknown_model_makes_cost_unknown(self) -> None:
        records = [record("values-001"), record("values-002", cost_eur=None, unpriced_models=("m-unknown",))]

        report = render(records)

        assert "cost unknown (no price for m-unknown)" in report
        assert "total EUR" not in report

    def test_effort_and_schema_recall(self) -> None:
        records = [
            record("values-001", tool_calls=2, sql_attempts=1, schema_recall=True),
            record("values-002", tool_calls=4, sql_attempts=3, schema_recall=False),
            record("unanswerable-001", expected_outcome="unanswerable", outcome="unanswerable", schema_recall=None),
        ]

        report = render(records)

        assert "mean tool calls 2.7, mean SQL attempts 1.7" in report
        assert "Schema recall: 50.0% (1/2)" in report

    def test_failing_cases_list_diff_and_trace(self) -> None:
        records = [record(), record("metrics-007", passed=False, diff="row count differs", trace_id="abc123")]

        report = render(records)

        assert "| metrics-007 | answered | row count differs | abc123 |" in report
        assert "| values-001 |" not in report

    def test_a_diff_with_a_pipe_or_newline_stays_in_its_table_cell(self) -> None:
        records = [record("metrics-007", passed=False, diff="agent SQL failed: a | b\nsecond line", trace_id="abc123")]

        report = render(records)

        assert "| metrics-007 | answered | agent SQL failed: a \\| b second line | abc123 |" in report

    def test_ex_per_repeat_only_when_repeating(self) -> None:
        records = [record(repeat=1), record(repeat=2, passed=False)]
        repeating = META.model_copy(update={"repeat": 2})

        assert "| repeat 2 | 0.0% (0/1) |" in render(records, meta=repeating)
        assert "| repeat 1 |" not in render(records[:1])

    def test_header_names_the_run(self) -> None:
        report = render([record(), record("values-002", repeat=2)], meta=META.model_copy(update={"repeat": 2}))

        for text in ("oracle", "gold_version 3", "dataset_version 2", "2026-11-05", "repeats 2", "cases 2"):
            assert text in report

    def test_previous_run_diff_lists_newly_passing_and_failing(self) -> None:
        previous = [record("values-001", passed=False), record("values-002"), record("values-003")]
        current = [record("values-001"), record("values-002", passed=False), record("values-003")]

        report = render(current, previous)

        assert "20261101T000000Z-oracle" in report
        assert "EX change: +0.0 points" in report
        assert "Newly passing: values-001" in report
        assert "Newly failing: values-002" in report

    def test_ex_change_is_signed(self) -> None:
        previous = [record("values-001"), record("values-002")]
        current = [record("values-001"), record("values-002", passed=False)]

        assert "EX change: -50.0 points" in render(current, previous)

    def test_without_a_previous_run_the_report_says_so(self) -> None:
        report = render_report(META, [record()], None, None, "v0_baseline")

        assert "No earlier run of v0_baseline with gold_version 3 and dataset_version 2." in report

    def test_ex_change_compares_only_the_cases_both_runs_have(self) -> None:
        previous = [record("values-001"), record("values-002", passed=False), record("values-003", passed=False)]
        current = [record("values-001")]  # a --case run

        report = render(current, previous)

        assert "EX change: +0.0 points (over the 1 shared case)" in report


class TestPreviousRun:
    def make_run(self, runs_dir: Path, name: str, meta: RunMeta, records: list[CaseRecord]) -> None:
        directory = runs_dir / name
        directory.mkdir(parents=True)
        (directory / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
        (directory / "cases.jsonl").write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")

    def test_newest_run_with_the_same_versions_wins(self, tmp_path: Path) -> None:
        self.make_run(tmp_path, "20261101T000000Z-oracle", META, [record("values-001")])
        self.make_run(tmp_path, "20261103T000000Z-oracle", META, [record("values-002")])

        found = previous_run(tmp_path, "oracle", 3, 2)

        assert found is not None
        assert found[0] == "20261103T000000Z-oracle"
        assert [r.case_id for r in found[1]] == ["values-002"]

    def test_a_run_with_another_gold_version_is_ignored(self, tmp_path: Path) -> None:
        self.make_run(tmp_path, "20261101T000000Z-oracle", META, [record("values-001")])
        other = META.model_copy(update={"gold_version": 4})
        self.make_run(tmp_path, "20261103T000000Z-oracle", other, [record("values-002")])

        found = previous_run(tmp_path, "oracle", 3, 2)

        assert found is not None
        assert found[0] == "20261101T000000Z-oracle"

    def test_a_run_of_another_variant_is_ignored(self, tmp_path: Path) -> None:
        self.make_run(tmp_path, "20261103T000000Z-v1_other", META.model_copy(update={"variant": "v1_other"}), [])

        assert previous_run(tmp_path, "oracle", 3, 2) is None

    def test_a_run_without_cases_jsonl_is_skipped(self, tmp_path: Path) -> None:
        self.make_run(tmp_path, "20261101T000000Z-oracle", META, [record("values-001")])
        committed_only = tmp_path / "20261103T000000Z-oracle"
        committed_only.mkdir()
        (committed_only / "meta.json").write_text(META.model_dump_json(), encoding="utf-8")
        (committed_only / "report.md").write_text("# report\n", encoding="utf-8")

        found = previous_run(tmp_path, "oracle", 3, 2)

        assert found is not None
        assert found[0] == "20261101T000000Z-oracle"

    def test_only_runs_without_cases_jsonl_gives_none(self, tmp_path: Path) -> None:
        committed_only = tmp_path / "20261103T000000Z-oracle"
        committed_only.mkdir()
        (committed_only / "meta.json").write_text(META.model_dump_json(), encoding="utf-8")

        assert previous_run(tmp_path, "oracle", 3, 2) is None

    def test_missing_runs_dir_gives_none(self, tmp_path: Path) -> None:
        assert previous_run(tmp_path / "runs", "oracle", 3, 2) is None


class TestWriteRun:
    def test_written_run_reads_back_as_the_previous_run(self, tmp_path: Path) -> None:
        records = [record("values-001", cost_eur=Decimal("0.0123")), record("values-002", passed=False, diff="x")]

        directory = write_run(tmp_path, META, records, "# report\n")

        assert directory.name == "20261105T093000Z-oracle"
        assert (directory / "report.md").read_text(encoding="utf-8") == "# report\n"
        lines = (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["case_id"] for line in lines] == ["values-001", "values-002"]
        found = previous_run(tmp_path, "oracle", 3, 2)
        assert found is not None
        assert found[1] == records

    def test_two_runs_in_the_same_second_keep_both_and_the_later_is_newer(self, tmp_path: Path) -> None:
        first = write_run(tmp_path, META, [record("values-001")], "# one\n")
        second = write_run(tmp_path, META, [record("values-002")], "# two\n")

        found = previous_run(tmp_path, "oracle", 3, 2)

        assert first != second
        assert (first / "report.md").read_text(encoding="utf-8") == "# one\n"
        assert found is not None
        assert found[0] == second.name
        assert [r.case_id for r in found[1]] == ["values-002"]
