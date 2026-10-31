from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

TESTS_ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def selected_files(request: pytest.FixtureRequest) -> frozenset[Path]:
    return frozenset(Path(str(item.path)).resolve() for item in request.session.items)


def test_no_test_directory_is_skipped_by_pytest_defaults(request: pytest.FixtureRequest) -> None:
    patterns = request.config.getini("norecursedirs")

    skipped = [path.name for path in _directories(TESTS_ROOT) if any(path.match(pattern) for pattern in patterns)]

    assert skipped == []


def test_every_test_file_is_collected(
    request: pytest.FixtureRequest, selected_files: frozenset[Path], deselected_files: frozenset[Path]
) -> None:
    # The cache plugin (--lf) can be disabled with -p no:cacheprovider, which leaves its option undefined.
    config = request.config
    if config.getoption("lf", False) or config.getoption("ignore", None) or config.getoption("ignore_glob", None):
        pytest.skip("--lf, --ignore or --ignore-glob left some test files out on purpose")

    arguments = [Path(str(argument).split("::")[0]).resolve() for argument in request.config.args]
    if not any(argument == TESTS_ROOT or argument in TESTS_ROOT.parents for argument in arguments):
        pytest.skip("only part of ui/backend/tests was selected")

    on_disk = {path.resolve() for path in TESTS_ROOT.rglob("test_*.py")}

    uncollected = on_disk - selected_files - deselected_files

    assert sorted(path.relative_to(TESTS_ROOT).as_posix() for path in uncollected) == []


def _directories(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_dir() and path.name != "__pycache__":
            yield path
