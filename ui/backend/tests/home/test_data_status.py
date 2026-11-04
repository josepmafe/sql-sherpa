from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sherpa_ui.home.data_status import FreshnessReader, FreshnessUnavailable, failed_node_count, failing_test_count
from ui_helpers import make_last_full_build, make_node_result

if TYPE_CHECKING:
    from pathlib import Path

GENERATED_AT = "2026-11-01T14:40:00.167466Z"


def _result(unique_id: str, status: str, seconds_ago: float | None = 100.0) -> dict[str, object]:
    row: dict[str, object] = {"unique_id": unique_id, "status": status}
    if seconds_ago is not None:
        row["max_loaded_at"] = "2026-10-26T00:00:00+00:00"
        row["max_loaded_at_time_ago_in_s"] = seconds_ago
    return row


def _write(path: Path, results: list[dict[str, object]]) -> FreshnessReader:
    document = {"metadata": {"generated_at": GENERATED_AT}, "results": results}
    (path / "sources.json").write_text(json.dumps(document), encoding="utf-8")
    return FreshnessReader(path)


class TestFreshnessReader:
    def test_missing_file_gives_none(self, tmp_path: Path) -> None:
        assert FreshnessReader(tmp_path).read() is None

    def test_all_passing_has_no_problems(self, tmp_path: Path) -> None:
        reader = _write(tmp_path, [_result("source.sherpa.crm.leads", "pass")])

        freshness = reader.read()

        assert freshness is not None
        assert freshness.problems == ()
        assert freshness.checked_at == datetime(2026, 11, 1, 14, 40, 0, 167466, tzinfo=UTC)

    def test_problems_list_errors_first_with_short_names(self, tmp_path: Path) -> None:
        reader = _write(
            tmp_path,
            [
                _result("source.sherpa.marketing.ad_spend_daily", "warn", 49.5 * 3600),
                _result("source.sherpa.crm.leads", "pass"),
                _result("source.sherpa.web.sessions", "error", 100 * 3600),
                _result("source.sherpa.crm.accounts", "runtime error", None),
                _result("source.sherpa.crm.contacts", "warn", 40 * 3600),
            ],
        )

        freshness = reader.read()

        assert freshness is not None
        assert [(p.name, p.status, p.hours_ago) for p in freshness.problems] == [
            ("crm.accounts", "error", None),
            ("web.sessions", "error", 100),
            ("crm.contacts", "warn", 40),
            ("marketing.ad_spend_daily", "warn", 49),
        ]
        assert freshness.problems[0].max_loaded_at is None
        assert freshness.problems[3].max_loaded_at == datetime(2026, 10, 26, tzinfo=UTC)

    def test_it_reads_on_every_call(self, tmp_path: Path) -> None:
        reader = _write(tmp_path, [])
        first = reader.read()
        _write(tmp_path, [_result("source.sherpa.crm.leads", "error")])

        second = reader.read()

        assert first is not None
        assert first.problems == ()
        assert second is not None
        assert len(second.problems) == 1

    @pytest.mark.parametrize(
        "content",
        ['{"x":', "[]", json.dumps({"metadata": {"generated_at": GENERATED_AT}, "results": [{"unique_id": "a"}]})],
        ids=["truncated", "list-at-top", "result-without-status"],
    )
    def test_unreadable_file_raises(self, tmp_path: Path, content: str) -> None:
        (tmp_path / "sources.json").write_text(content, encoding="utf-8")

        with pytest.raises(FreshnessUnavailable):
            FreshnessReader(tmp_path).read()


class TestFailingTestCount:
    def test_counts_failed_tests_and_unit_tests_only(self) -> None:
        build = make_last_full_build(
            make_node_result("test.a", "fail"),
            make_node_result("test.b", "error"),
            make_node_result("test.c", "runtime error"),
            make_node_result("unit_test.d", "fail"),
            make_node_result("test.e", "warn"),
            make_node_result("test.f", "pass"),
            make_node_result("test.g", "skipped"),
            make_node_result("model.h", "error"),
        )

        assert failing_test_count(build) == 4

    def test_empty_build_has_none(self) -> None:
        assert failing_test_count(make_last_full_build()) == 0


class TestFailedNodeCount:
    def test_counts_failed_models_seeds_and_snapshots_only(self) -> None:
        build = make_last_full_build(
            make_node_result("model.a", "error"),
            make_node_result("seed.b", "error"),
            make_node_result("snapshot.c", "partial success"),
            make_node_result("model.d", "runtime error"),
            make_node_result("model.e", "success"),
            make_node_result("model.f", "skipped"),
            make_node_result("model.g", "no-op"),
            make_node_result("test.h", "fail"),
            make_node_result("test.i", "error"),
            make_node_result("test.j", "skipped"),
        )

        assert failed_node_count(build) == 4
        assert failing_test_count(build) == 2

    def test_a_model_build_with_an_error_and_a_skipped_test(self) -> None:
        build = make_last_full_build(make_node_result("model.x", "error"), make_node_result("test.y", "skipped"))

        assert (failed_node_count(build), failing_test_count(build)) == (1, 0)

    def test_empty_build_has_none(self) -> None:
        assert failed_node_count(make_last_full_build()) == 0
