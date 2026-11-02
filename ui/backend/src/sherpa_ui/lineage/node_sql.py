from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.lineage.manifest_graph import GraphNode, NodeTest

COMPILED_DIR = "compiled"
SQL_SUFFIX = ".sql"


class NodeSql:
    """Reads the compiled SQL dbt wrote under `<target>/compiled`; `manifest.json` carries none (UI spec §6.5)."""

    def __init__(self, target_path: Path) -> None:
        self._root = (target_path / COMPILED_DIR).resolve()

    def compiled(self, node: GraphNode) -> str | None:
        """None when the file is missing, unreadable or the node's path would leave `compiled/`."""
        # `file_path` already uses forward slashes, whatever OS wrote the manifest. A node defined in YAML (a snapshot)
        # is compiled to `<yaml path>/<name>.sql`.
        if node.file_path.endswith(SQL_SUFFIX):
            relative = node.file_path
        else:
            relative = f"{node.file_path}/{node.name}{SQL_SUFFIX}"

        return self._read(node.package_name, relative)

    def compiled_test(self, test: NodeTest) -> str | None:
        """A singular test is compiled to its own file, a generic one to `<its YAML>/<path>`; a unit test is not
        compiled. None when the file is missing or `path` would leave `compiled/`."""
        match test.kind:
            case "singular":
                return self._read(test.package_name, test.file_path)
            case "generic" if test.path is not None:
                return self._read(test.package_name, f"{test.file_path}/{test.path}")
            case _:
                return None

    def _read(self, package: str, relative: str) -> str | None:
        try:
            base = (self._root / package).resolve()  # the package folder itself must stay under compiled/
            path = (base / relative).resolve()
            if not base.is_relative_to(self._root) or not path.is_relative_to(base) or not path.is_file():
                return None

            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable, undecodable, or a NUL in the path
            return None
