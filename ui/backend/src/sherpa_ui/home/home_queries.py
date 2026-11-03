"""The curated SQL behind the Home page (UI spec §6.2), read from `home_queries.yaml`. Every query runs through the
SQL guard and the read-only runner like any other, and reads only the marts the `sherpa_home` exposure lists."""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

HOME_QUERIES_PATH: Final = Path(__file__).with_name("home_queries.yaml")
MAX_DECIMALS: Final = 2

TileFormat = Literal["currency", "count", "percent"]
ChangeKind = Literal["relative", "points"]
Direction = Literal["up", "down", "flat"]


class TileQuery(BaseModel):
    """One tile: how it reads, and the query returning one row of `this_period` and `last_period`."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    format: TileFormat
    decimals: int = Field(default=0, ge=0, le=MAX_DECIMALS)
    up_is_good: bool
    note: str | None = None
    sql: str


class HomeQueries(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: str
    tiles: tuple[TileQuery, ...]
    trend: str

    @classmethod
    def from_yaml(cls, path: Path) -> HomeQueries:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    @model_validator(mode="after")
    def _tile_ids_are_unique(self) -> Self:
        ids = [tile.id for tile in self.tiles]
        duplicates = sorted({tile_id for tile_id in ids if ids.count(tile_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate tile ids: {', '.join(duplicates)}")
        return self
