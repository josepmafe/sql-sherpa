from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Final

from sherpa_mcp.catalog import CATALOG_FILE, MANIFEST_FILE, CatalogUnavailable
from sqlglot.errors import SqlglotError

from sherpa_ui.lineage.column_lineage import ColumnLineage, ColumnLineageBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.lineage.manifest_graph import ManifestGraph
    from sherpa_ui.lineage.manifest_loader import ManifestLoader
    from sherpa_ui.lineage.node_sql import NodeSql

logger = logging.getLogger(__name__)

SQL_RESOURCE_TYPES: Final = frozenset({"model", "snapshot"})  # the nodes with a compiled file
CATALOG_SECTIONS: Final = ("nodes", "sources")
NOTE_NO_CATALOG: Final = f"{CATALOG_FILE} is not in the dbt target, so the columns are the documented ones"
_MISSING_HINT = "run `make build-prod`"

type _Stamp = tuple[int, int | None]  # (manifest mtime, catalog mtime; None when there is no catalog)


class ColumnLineageLoader:
    """Builds the ColumnLineage from the manifest, the catalog and the compiled SQL, and rebuilds it when the
    manifest or the catalog changes.

    Follows `ManifestLoader`'s contract: a rebuild that fails keeps the last lineage, with a warning, and is tried
    again on the next call; only a loader that never built one raises `CatalogUnavailable`. A lock makes callers that
    arrive while a build runs wait for it instead of starting their own. Blocking: call it from a worker thread.
    """

    def __init__(self, target_path: Path, manifests: ManifestLoader, node_sql: NodeSql) -> None:
        self._manifest_path = target_path / MANIFEST_FILE
        self._catalog_path = target_path / CATALOG_FILE
        self._manifests = manifests
        self._node_sql = node_sql
        self._lock = threading.Lock()
        self._stamp: _Stamp | None = None
        self._lineage: ColumnLineage | None = None

    def load(self) -> ColumnLineage:
        with self._lock:
            try:
                stamp = self._stamp_of()  # before the load, so a rewrite in between is built again on the next call
                graph = self._manifests.load()
                if self._lineage is None or stamp != self._stamp:
                    self._lineage = self._build(graph)
                    self._stamp = stamp

                return self._lineage
            except (CatalogUnavailable, SqlglotError) as exc:
                if self._lineage is None:
                    if isinstance(exc, SqlglotError):
                        raise CatalogUnavailable(f"cannot build the column lineage: {exc}") from exc

                    raise

                logger.warning("could not rebuild the column lineage, so the previous one stays in use: %s", exc)
                return self._lineage

    def _build(self, graph: ManifestGraph) -> ColumnLineage:
        catalog = self._read_catalog()
        columns = {unique_id: [column.name for column in node.columns] for unique_id, node in graph.nodes.items()}
        if catalog is None:
            note = NOTE_NO_CATALOG
        else:
            columns.update(_catalog_columns(catalog, graph))
            note = None

        compiled = {
            unique_id: self._node_sql.compiled(node)
            for unique_id, node in graph.nodes.items()
            if node.resource_type in SQL_RESOURCE_TYPES
        }
        return ColumnLineageBuilder(graph, columns, columns_note=note).build(compiled)

    def _read_catalog(self) -> dict[str, Any] | None:
        """None when the file does not exist; a file that exists but cannot be read is an error."""
        if not self._catalog_path.exists():
            return None

        try:
            catalog = json.loads(self._catalog_path.read_text(encoding="utf-8"))
            if not isinstance(catalog, dict):
                raise TypeError("not an object")

            return catalog
        except (OSError, ValueError, TypeError) as exc:
            raise CatalogUnavailable(f"cannot read {CATALOG_FILE}: {_MISSING_HINT}") from exc

    def _stamp_of(self) -> _Stamp:
        try:
            manifest_stamp = self._manifest_path.stat().st_mtime_ns
        except OSError as exc:
            raise CatalogUnavailable(f"{MANIFEST_FILE} not found: {_MISSING_HINT}") from exc

        try:
            catalog_stamp: int | None = self._catalog_path.stat().st_mtime_ns
        except OSError:
            catalog_stamp = None

        return manifest_stamp, catalog_stamp


def _catalog_columns(catalog: dict[str, Any], graph: ManifestGraph) -> dict[str, list[str]]:
    """Each graph node's columns in table order (the catalog's `index`); a node the catalog lacks is left out."""
    try:
        columns: dict[str, list[str]] = {}
        for section in CATALOG_SECTIONS:
            for unique_id, raw in catalog.get(section, {}).items():
                if unique_id in graph.nodes:
                    ordered = sorted(raw.get("columns", {}).values(), key=lambda column: column["index"])
                    columns[unique_id] = [column["name"] for column in ordered]
        return columns
    except (KeyError, TypeError, AttributeError) as exc:
        raise CatalogUnavailable(f"unexpected layout in {CATALOG_FILE}: {_MISSING_HINT}") from exc
