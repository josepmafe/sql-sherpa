from __future__ import annotations

import sys
from datetime import date
from typing import TYPE_CHECKING

import backfill as backfill_module
import pytest
from backfill import BackfillError, ChunkedBackfill, _run, chunk_sizes, default_start_date, main

if TYPE_CHECKING:
    from collections.abc import Sequence

START_DATE = date(2025, 1, 1)
PROBE = ["sherpa-generate", "start-date"]
SNAPSHOT = "snapshot"


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
