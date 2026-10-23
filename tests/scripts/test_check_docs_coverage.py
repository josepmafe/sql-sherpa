from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from check_docs_coverage import DocsCoverageChecker, main

if TYPE_CHECKING:
    from pathlib import Path

STAGING = "models/staging"


class TestDocsCoverageChecker:
    def test_reports_model_without_description(self) -> None:
        manifest = _manifest(_model("stg_shop__orders", "", {"order_id": "Primary key."}))
        catalog = _catalog({"stg_shop__orders": ["order_id"]})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == ["stg_shop__orders"]

    def test_reports_catalog_column_missing_from_yaml(self) -> None:
        manifest = _manifest(_model("stg_shop__orders", "One row per order.", {"order_id": "Primary key."}))
        catalog = _catalog({"stg_shop__orders": ["order_id", "placed_at"]})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == ["stg_shop__orders.placed_at"]

    def test_reports_blank_description_as_missing(self) -> None:
        manifest = _manifest(_model("stg_shop__orders", " \n\t", {"order_id": "  "}))
        catalog = _catalog({"stg_shop__orders": ["order_id"]})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == ["stg_shop__orders", "stg_shop__orders.order_id"]

    def test_ignores_models_outside_prefix(self) -> None:
        manifest = _manifest(
            _model("stg_shop__orders", "One row per order.", {"order_id": "Primary key."}),
            _model("dim_date", "", {"date_day": ""}, path="models/marts/shared/dim_date.sql"),
        )
        catalog = _catalog({"stg_shop__orders": ["order_id"], "dim_date": ["date_day", "year"]})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == []

    def test_matches_prefix_in_windows_manifest_paths(self) -> None:
        manifest = _manifest(_model("stg_shop__orders", "", {}, path="models\\staging\\shop\\stg_shop__orders.sql"))
        catalog = _catalog({"stg_shop__orders": []})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == ["stg_shop__orders"]

    def test_clean_project_returns_empty(self) -> None:
        manifest = _manifest(
            _model("stg_shop__orders", "One row per order.", {"order_id": "Primary key.", "placed_at": "UTC."}),
        )
        catalog = _catalog({"stg_shop__orders": ["order_id", "placed_at"]})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == []

    def test_model_missing_from_catalog_is_checked_on_yaml_columns_only(self) -> None:
        manifest = _manifest(_model("stg_shop__orders", "One row per order.", {"order_id": ""}))
        catalog = _catalog({})

        result = DocsCoverageChecker(manifest, catalog).undocumented(STAGING)

        assert result == ["stg_shop__orders.order_id"]


class TestMain:
    def test_exits_1_and_lists_undocumented_models_and_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_artifacts(
            tmp_path,
            _manifest(_model("stg_shop__orders", "", {"order_id": "Primary key."})),
            _catalog({"stg_shop__orders": ["order_id", "placed_at"]}),
        )
        monkeypatch.setattr(sys, "argv", ["check_docs_coverage", "--target-path", str(tmp_path)])

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code == 1
        assert capsys.readouterr().out == "Missing descriptions:\n  stg_shop__orders\n  stg_shop__orders.placed_at\n"

    def test_clean_project_prints_nothing_and_does_not_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_artifacts(
            tmp_path,
            _manifest(_model("stg_shop__orders", "One row per order.", {"order_id": "Primary key."})),
            _catalog({"stg_shop__orders": ["order_id"]}),
        )
        monkeypatch.setattr(sys, "argv", ["check_docs_coverage", "--target-path", str(tmp_path)])

        main()

        assert capsys.readouterr().out == ""


def _write_artifacts(target: Path, manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


def _model(name: str, description: str, columns: dict[str, str], path: str | None = None) -> dict[str, Any]:
    return {
        "unique_id": f"model.sherpa.{name}",
        "resource_type": "model",
        "name": name,
        "original_file_path": path or f"{STAGING}/shop/{name}.sql",
        "description": description,
        "columns": {column: {"name": column, "description": text} for column, text in columns.items()},
    }


def _manifest(*models: dict[str, Any]) -> dict[str, Any]:
    return {"nodes": {model["unique_id"]: model for model in models}}


def _catalog(columns_by_model: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "nodes": {
            f"model.sherpa.{name}": {
                "columns": {column: {"name": column, "index": i} for i, column in enumerate(columns, start=1)}
            }
            for name, columns in columns_by_model.items()
        }
    }
