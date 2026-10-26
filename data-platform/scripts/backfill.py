"""Simulate a range of days in weekly chunks, taking a dbt snapshot after each one (data platform spec §6.3).

A snapshot only sees the state of the data at the moment it runs, so a catalog change made during day 40 is
captured only if a snapshot runs between day 40 and the days that follow.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dbt.cli.main import dbtRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CHUNK_DAYS = 7  # data platform spec §6.3
DEFAULT_SEED = 42
DEFAULT_SCALE = 1.0
ARGUMENT_ERROR_EXIT_CODE = 2
DBT_PROJECT_DIR = Path(__file__).resolve().parents[1] / "dbt"
GENERATOR = "sherpa-generate"


class BackfillError(Exception):
    """A failure the user can act on: printed as one line, exit code 2."""


class ChunkedBackfill:
    def __init__(self, command: Callable[[Sequence[str]], str], snapshot: Callable[[], None]) -> None:
        self._command = command
        self._snapshot = snapshot

    def backfill(self, days: int, start_date: date, *, seed: int, scale: float) -> None:
        existing = self._command([GENERATOR, "start-date"]).strip()
        if existing:
            msg = (
                f"a simulation already runs from {existing}; make backfill starts a new one (make reset first), "
                "make advance DAYS=<n> continues it"
            )
            raise BackfillError(msg)

        for index, size in enumerate(chunk_sizes(days)):
            args = [GENERATOR, "generate", "--days", str(size), "--seed", str(seed), "--scale", str(scale)]
            if index == 0:
                args += ["--start-date", start_date.isoformat()]

            self._command(args)
            self._snapshot()


class DbtSnapshot:
    """`dbt snapshot` in-process: the project is parsed once, not once per chunk (about 4 s each)."""

    def __init__(self, project_dir: Path, target: Literal["prod", "eval"]) -> None:
        self._args = [
            "--target",
            target,
            "--target-path",
            f"target-{target}",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir),
        ]
        parsed = dbtRunner().invoke(["parse", *self._args])
        if not parsed.success:
            msg = "dbt parse failed"
            raise BackfillError(msg) from parsed.exception

        self._manifest = parsed.result

    def __call__(self) -> None:
        result = dbtRunner(manifest=self._manifest).invoke(["snapshot", *self._args])
        if not result.success:
            msg = "dbt snapshot failed"
            raise BackfillError(msg) from result.exception


def chunk_sizes(days: int, chunk_days: int = CHUNK_DAYS) -> list[int]:
    """Full chunks of `chunk_days`, then a shorter one for the remainder."""
    full, rest = divmod(days, chunk_days)
    return [chunk_days] * full + ([rest] if rest else [])


def default_start_date(days: int, today: date) -> date:
    """The start that makes the last simulated day yesterday."""
    return today - timedelta(days=days)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=_positive_int, required=True, help="days to simulate")
    parser.add_argument("--start-date", type=date.fromisoformat, help="first day (default: ends yesterday)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--target", choices=["prod", "eval"], default="prod", help="dbt target to snapshot")
    args = parser.parse_args(argv)
    start_date = args.start_date or default_start_date(args.days, datetime.now(UTC).date())

    try:
        ChunkedBackfill(_run, DbtSnapshot(DBT_PROJECT_DIR, args.target)).backfill(
            args.days, start_date, seed=args.seed, scale=args.scale
        )
    except BackfillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return ARGUMENT_ERROR_EXIT_CODE

    return 0


def _run(args: Sequence[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        msg = f"{' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        raise BackfillError(msg)

    return result.stdout


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        msg = "must be at least 1"
        raise argparse.ArgumentTypeError(msg)

    return number


if __name__ == "__main__":
    sys.exit(main())
