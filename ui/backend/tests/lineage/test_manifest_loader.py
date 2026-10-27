from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_ui.lineage.manifest_loader import ManifestLoader

if TYPE_CHECKING:
    from pathlib import Path


class TestManifestLoader:
    def test_reloads_when_the_file_changes(self, tmp_path: Path, manifest: dict[str, Any]) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        loader = ManifestLoader(tmp_path)
        first = loader.load()

        assert loader.load() is first

        del manifest["nodes"]["model.p.dim_date"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        _bump_mtime(path)
        second = loader.load()

        assert second is not first
        assert "model.p.dim_date" not in second.nodes

    def test_keeps_the_last_graph_when_a_reload_fails(
        self, tmp_path: Path, manifest: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "manifest.json"
        text = json.dumps(manifest)
        path.write_text(text, encoding="utf-8")
        loader = ManifestLoader(tmp_path)
        first = loader.load()

        path.write_text(text[: len(text) // 2], encoding="utf-8")  # half-written by a running build
        _bump_mtime(path)
        with caplog.at_level("WARNING"):
            kept = loader.load()

        assert kept is first
        assert "stays in use" in caplog.text

        path.write_text(text, encoding="utf-8")  # the build finished
        _bump_mtime(path)

        assert loader.load() is not first

    def test_raises_when_it_never_loaded(self, tmp_path: Path) -> None:
        loader = ManifestLoader(tmp_path)

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            loader.load()

    def test_raises_on_an_unexpected_layout_when_it_never_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        loader = ManifestLoader(tmp_path)

        with pytest.raises(CatalogUnavailable, match="unexpected layout"):
            loader.load()


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
