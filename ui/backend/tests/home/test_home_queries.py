from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlglot
import yaml
from sherpa_mcp.sql_guard import SqlGuard
from sherpa_ui.home.home_queries import HOME_QUERIES_PATH, HomeQueries
from sqlglot import exp
from ui_helpers import REPO_ROOT, data_platform_marts

if TYPE_CHECKING:
    from pathlib import Path

EXPOSURES = REPO_ROOT / "data-platform" / "dbt" / "models" / "marts" / "_exposures.yml"
HOME_SQL = {
    "period": HomeQueries.from_yaml(HOME_QUERIES_PATH).period,
    "trend": HomeQueries.from_yaml(HOME_QUERIES_PATH).trend,
    **{tile.id: tile.sql for tile in HomeQueries.from_yaml(HOME_QUERIES_PATH).tiles},
}


def test_tiles_are_the_specs() -> None:
    tiles = HomeQueries.from_yaml(HOME_QUERIES_PATH).tiles

    assert tuple(tile.id for tile in tiles) == ("net_revenue", "orders", "aov", "refund_rate", "new_customers")
    assert tuple(tile.format for tile in tiles) == ("currency", "count", "currency", "percent", "count")
    assert [tile.id for tile in tiles if not tile.up_is_good] == ["refund_rate"]
    assert [tile.id for tile in tiles if tile.note] == ["net_revenue", "aov", "refund_rate"]


@pytest.mark.parametrize("tile_id", ["net_revenue", "aov"])
def test_the_as_stood_notes_explain_the_30_days_and_the_refunds(tile_id: str) -> None:
    notes = {tile.id: tile.note for tile in HomeQueries.from_yaml(HOME_QUERIES_PATH).tiles}

    note = notes[tile_id] or ""
    assert "30 days" in note
    assert "refunds" in note


@pytest.mark.parametrize("name", list(HOME_SQL))
def test_every_home_query_passes_the_guard(name: str) -> None:
    result = SqlGuard(data_platform_marts()).check(HOME_SQL[name])

    assert result.ok, result.errors


def test_sherpa_home_lists_exactly_the_marts_home_reads() -> None:
    read = {
        table.name
        for sql in HOME_SQL.values()
        for table in sqlglot.parse_one(sql, read="postgres").find_all(exp.Table)
        if table.db == "marts"
    }
    exposure = next(
        e for e in yaml.safe_load(EXPOSURES.read_text(encoding="utf-8"))["exposures"] if e["name"] == "sherpa_home"
    )

    listed = {ref.removeprefix("ref('").removesuffix("')") for ref in exposure["depends_on"]}
    assert listed == read
    assert exposure["type"] == "dashboard"


def test_duplicate_tile_ids_are_refused(tmp_path: Path) -> None:
    tile = {"id": "orders", "title": "Orders", "format": "count", "up_is_good": True, "sql": "select 1"}
    path = tmp_path / "home.yaml"
    path.write_text(
        yaml.safe_dump({"period": "select 1", "tiles": [tile, tile], "trend": "select 1"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="orders"):
        HomeQueries.from_yaml(path)
