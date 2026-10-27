from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sherpa_mcp.catalog import MANIFEST_FILE, CatalogUnavailable

from sherpa_ui.lineage.manifest_graph import ManifestGraph

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MISSING_HINT = "run `make build-prod`"


class ManifestLoader:
    """Builds the ManifestGraph from `manifest.json` and rebuilds it when the file changes.

    Follows `MartCatalogLoader`'s contract: a rebuild that fails (a file half-written by a running build, or gone)
    keeps the last graph, with a warning, and is tried again on the next call; only a loader that never loaded one
    raises `CatalogUnavailable`.
    """

    def __init__(self, target_path: Path) -> None:
        self._path = target_path / MANIFEST_FILE
        self._stamp: int | None = None
        self._graph: ManifestGraph | None = None

    def load(self) -> ManifestGraph:
        try:
            stamp = self._mtime()
            if self._graph is None or stamp != self._stamp:
                self._graph = self._read_graph()
                self._stamp = stamp
            return self._graph
        except CatalogUnavailable as exc:
            if self._graph is None:
                raise

            logger.warning(
                "could not reload %s, so the graph generated at %s stays in use: %s",
                MANIFEST_FILE,
                self._graph.generated_at.isoformat(),
                exc,
            )
            return self._graph

    def _read_graph(self) -> ManifestGraph:
        try:
            manifest: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CatalogUnavailable(f"cannot read {MANIFEST_FILE}: {_MISSING_HINT}") from exc

        try:
            return ManifestGraph.from_manifest(manifest)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise CatalogUnavailable(f"unexpected layout in {MANIFEST_FILE}: {_MISSING_HINT}") from exc

    def _mtime(self) -> int:
        try:
            return self._path.stat().st_mtime_ns
        except OSError as exc:
            raise CatalogUnavailable(f"{MANIFEST_FILE} not found: {_MISSING_HINT}") from exc
