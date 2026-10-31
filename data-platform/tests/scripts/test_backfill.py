from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import backfill as backfill_module
import pytest
from backfill import BackfillError, ChunkedBackfill, DbtSnapshot, _run, chunk_sizes, default_start_date, main

if TYPE_CHECKING:
    from collections.abc import Sequence

START_DATE = date(2025, 1, 1)
PROBE = ["sherpa-generate", "start-date"]
SNAPSHOT = "snapshot"
PROJECT_DIR = Path("dbt-project")


class FakeCommand:
    """Records every call into a shared list; the probe answers `probe_output`, a generate call can fail."""

    def __init__(self, calls: list[object], probe_output: str = "", fail_on_generate: int | None = None) -> None:
        self._calls = calls
        self._probe_output = probe_output
        self._fail_on_generate = fail_on_generate
        self._generates = 0

    def __call__(self, args: Sequence[str]) -> str:
        self._calls.append(list(args))
        if list(args) == PROBE:
            return self._probe_output

        self._generates += 1
        if self._generates == self._fail_on_generate:
            msg = "generate failed"
            raise BackfillError(msg)

        return "simulated\n"


class FakeDbtResult:
    def __init__(self, success: bool, result: object = None, exception: Exception | None = None) -> None:
        self.success = success
        self.result = result
        self.exception = exception


class FakeDbtRunner:
    """Stands in for dbtRunner: records (manifest, args) per invoke; `results` maps the dbt command to its result."""

    invocations: list[tuple[object, list[str]]] = []  # noqa: RUF012
    results: dict[str, FakeDbtResult] = {}  # noqa: RUF012

    def __init__(self, manifest: object = None) -> None:
        self._manifest = manifest

    def invoke(self, args: list[str]) -> FakeDbtResult:
        FakeDbtRunner.invocations.append((self._manifest, list(args)))
        return FakeDbtRunner.results.get(args[0], FakeDbtResult(True, result="manifest"))


@pytest.fixture
def fake_dbt(monkeypatch: pytest.MonkeyPatch) -> type[FakeDbtRunner]:
    FakeDbtRunner.invocations = []
    FakeDbtRunner.results = {}
    monkeypatch.setattr(backfill_module, "dbtRunner", FakeDbtRunner)
    return FakeDbtRunner


def _option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


@pytest.mark.parametrize(
    ("days", "expected"),
    [(365, [7] * 52 + [1]), (14, [7, 7]), (3, [3]), (1, [1])],
)
def test_chunk_sizes_are_weeks_with_a_short_last_chunk(days: int, expected: list[int]) -> None:
    assert chunk_sizes(days) == expected


def test_default_start_date_makes_the_last_day_yesterday() -> None:
    assert default_start_date(365, date(2026, 10, 25)) == date(2025, 10, 25)


def test_main_rejects_zero_days() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--days", "0"])

    assert exc.value.code == 2


class TestChunkedBackfill:
    def test_passes_the_start_date_only_to_the_first_chunk_and_snapshots_after_each(self) -> None:
        calls: list[object] = []
        backfill = ChunkedBackfill(FakeCommand(calls), lambda: calls.append(SNAPSHOT))

        backfill.backfill(15, START_DATE, seed=42, scale=1.0)

        generate = ["sherpa-generate", "generate", "--days"]
        tail = ["--seed", "42", "--scale", "1.0"]
        assert calls == [
            PROBE,
            [*generate, "7", *tail, "--start-date", "2025-01-01"],
            SNAPSHOT,
            [*generate, "7", *tail],
            SNAPSHOT,
            [*generate, "1", *tail],
            SNAPSHOT,
        ]

    def test_refuses_a_simulation_that_has_already_started(self) -> None:
        calls: list[object] = []
        backfill = ChunkedBackfill(FakeCommand(calls, probe_output="2025-01-01\n"), lambda: calls.append(SNAPSHOT))

        with pytest.raises(BackfillError, match="make advance"):
            backfill.backfill(15, START_DATE, seed=42, scale=1.0)

        assert calls == [PROBE]

    def test_stops_at_a_failing_chunk_without_snapshotting_it(self) -> None:
        calls: list[object] = []
        backfill = ChunkedBackfill(FakeCommand(calls, fail_on_generate=2), lambda: calls.append(SNAPSHOT))

        with pytest.raises(BackfillError, match="generate failed"):
            backfill.backfill(15, START_DATE, seed=42, scale=1.0)

        assert calls.count(SNAPSHOT) == 1

    def test_a_failing_snapshot_stops_the_chunks(self) -> None:
        calls: list[object] = []
        snapshots = 0

        def snapshot() -> None:
            nonlocal snapshots
            snapshots += 1
            calls.append(SNAPSHOT)
            if snapshots == 2:
                msg = "dbt snapshot failed"
                raise BackfillError(msg)

        backfill = ChunkedBackfill(FakeCommand(calls), snapshot)

        with pytest.raises(BackfillError, match="dbt snapshot failed"):
            backfill.backfill(22, START_DATE, seed=42, scale=1.0)

        generates = [c for c in calls if isinstance(c, list) and c[1] == "generate"]
        assert len(generates) == 2
        assert calls.count(SNAPSHOT) == 2


class TestDbtSnapshot:
    def test_parses_once_and_snapshots_per_call(self, fake_dbt: type[FakeDbtRunner]) -> None:
        snapshot = DbtSnapshot(PROJECT_DIR, "prod")

        snapshot()
        snapshot()

        commands = [args[0] for _, args in fake_dbt.invocations]
        assert commands == ["parse", "snapshot", "snapshot"]
        assert [manifest for manifest, args in fake_dbt.invocations if args[0] == "snapshot"] == ["manifest"] * 2
        assert _option(fake_dbt.invocations[1][1], "--project-dir") == str(PROJECT_DIR)
        assert _option(fake_dbt.invocations[1][1], "--profiles-dir") == str(PROJECT_DIR)

    @pytest.mark.parametrize(("target", "target_path"), [("prod", "target-prod"), ("eval", "target-eval")])
    def test_target_path_follows_the_target(self, fake_dbt: type[FakeDbtRunner], target: Any, target_path: str) -> None:
        DbtSnapshot(PROJECT_DIR, target)()

        for _, args in fake_dbt.invocations:
            assert _option(args, "--target") == target
            assert _option(args, "--target-path") == target_path

    def test_a_failed_parse_raises_backfill_error(self, fake_dbt: type[FakeDbtRunner]) -> None:
        cause = RuntimeError("bad yaml")
        fake_dbt.results["parse"] = FakeDbtResult(False, exception=cause)

        with pytest.raises(BackfillError, match="dbt parse failed") as exc:
            DbtSnapshot(PROJECT_DIR, "prod")

        assert exc.value.__cause__ is cause
        assert [args[0] for _, args in fake_dbt.invocations] == ["parse"]

    def test_a_failed_snapshot_raises_backfill_error(self, fake_dbt: type[FakeDbtRunner]) -> None:
        cause = RuntimeError("db down")
        fake_dbt.results["snapshot"] = FakeDbtResult(False, exception=cause)
        snapshot = DbtSnapshot(PROJECT_DIR, "prod")

        with pytest.raises(BackfillError, match="dbt snapshot failed") as exc:
            snapshot()

        assert exc.value.__cause__ is cause


class TestRun:
    def test_returns_stdout(self) -> None:
        assert _run([sys.executable, "-c", "print('hello')"]).strip() == "hello"

    def test_non_zero_exit_raises_with_the_exit_code(self) -> None:
        with pytest.raises(BackfillError, match=r"failed \(exit 3\)"):
            _run([sys.executable, "-c", "import sys; sys.exit(3)"])


class TestMain:
    def test_a_backfill_error_becomes_one_line_on_stderr_and_exit_code_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fail(*_: object, **__: object) -> None:
            msg = "boom"
            raise BackfillError(msg)

        monkeypatch.setattr(backfill_module, "DbtSnapshot", lambda *_: None)
        monkeypatch.setattr(ChunkedBackfill, "backfill", fail)

        code = main(["--days", "3"])

        assert code == 2
        assert capsys.readouterr().err == "error: boom\n"

    def test_main_passes_the_target_to_the_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[tuple[Path, str]] = []

        def fake_snapshot(project_dir: Path, target: str) -> None:
            seen.append((project_dir, target))

        monkeypatch.setattr(backfill_module, "DbtSnapshot", fake_snapshot)
        monkeypatch.setattr(ChunkedBackfill, "backfill", lambda *_, **__: None)

        code = main(["--days", "3", "--target", "eval"])

        assert code == 0
        assert seen == [(backfill_module.DBT_PROJECT_DIR, "eval")]

    def test_main_exits_2_on_a_dbt_failure(
        self, fake_dbt: type[FakeDbtRunner], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_dbt.results["parse"] = FakeDbtResult(False)

        code = main(["--days", "3"])

        assert code == 2
        assert capsys.readouterr().err == "error: dbt parse failed\n"
