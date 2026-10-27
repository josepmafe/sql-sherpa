from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.lineage.manifest_graph import GraphNode

COMPILED_DIR = "compiled"
SQL_SUFFIX = ".sql"


class NodeSql:
    """Reads the compiled SQL dbt wrote under `<target>/compiled`; `manifest.json` carries none (UI spec §6.5)."""

    def __init__(self, target_path: Path) -> None:
        self._root = (target_path / COMPILED_DIR).resolve()

    def compiled(self, node: GraphNode) -> str | None:
        """None when the file is missing or the node's path would leave `compiled/`."""
        # `file_path` already uses forward slashes, whatever OS wrote the manifest. A node defined in YAML (a snapshot)
        # is compiled to `<yaml path>/<name>.sql`.
        if node.file_path.endswith(SQL_SUFFIX):
            relative = node.file_path
        else:
            relative = f"{node.file_path}/{node.name}{SQL_SUFFIX}"

        path = (self._root / node.package_name / relative).resolve()
        if not path.is_relative_to(self._root) or not path.is_file():
            return None

        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None
