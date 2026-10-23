"""Fail when a dbt model or column under a path has no description.

Reads two artifacts `dbt docs generate` writes: manifest.json (what the YAML declares) and
catalog.json (the columns that actually exist in the database). A column that is in the
database but not in the YAML counts as undocumented too.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

DEFAULT_TARGET_PATH = "dbt/target"
DEFAULT_PATH_PREFIX = "models"


class DocsCoverageChecker:
    def __init__(self, manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
        self._manifest = manifest
        self._catalog = catalog

    def undocumented(self, path_prefix: str) -> list[str]:
        """`<model>` and `<model>.<column>` entries without a non-blank description, sorted."""
        missing: list[str] = []
        for unique_id, node in self._manifest["nodes"].items():
            if node["resource_type"] != "model" or not _is_under(node["original_file_path"], path_prefix):
                continue

            if _is_blank(node.get("description")):
                missing.append(node["name"])

            documented = node.get("columns", {})
            in_database = self._catalog["nodes"].get(unique_id, {}).get("columns", {})
            missing.extend(
                f"{node['name']}.{column}"
                for column in documented.keys() | in_database.keys()
                if _is_blank(documented.get(column, {}).get("description"))
            )

        return sorted(missing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-path", default=DEFAULT_TARGET_PATH, help="folder with manifest/catalog.json")
    parser.add_argument("--path", default=DEFAULT_PATH_PREFIX, help="only check models under this project path")
    args = parser.parse_args()

    target = Path(args.target_path)
    checker = DocsCoverageChecker(_read_json(target / "manifest.json"), _read_json(target / "catalog.json"))
    missing = checker.undocumented(args.path)
    if missing:
        print("Missing descriptions:", *missing, sep="\n  ")
        sys.exit(1)


def _is_under(file_path: str, prefix: str) -> bool:
    # dbt writes original_file_path with the OS separator; compare as POSIX paths.
    return PurePosixPath(PureWindowsPath(file_path).as_posix()).is_relative_to(prefix)


def _is_blank(description: str | None) -> bool:
    return not (description or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
