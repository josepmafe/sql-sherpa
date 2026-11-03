"""The comparison rules of the Home page (UI spec §6.2): pure functions from query rows to what the page shows."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from sherpa_ui.home.home_queries import ChangeKind, Direction, TileFormat

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_ui.home.home_queries import TileQuery

# The earlier window is (as_of - 60, as_of - 30]: its first day, as_of - 59, must have data.
EARLIER_WINDOW_START_DAYS: Final = 59
WEEK_LAST_DAY_OFFSET: Final = timedelta(days=6)  # a week starts on a Monday and ends on its Sunday


class Tile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    format: TileFormat
    decimals: int
    up_is_good: bool
    note: str | None
    value: Decimal | None
    change: Decimal | None  # relative (0.1 = +10%) or, for a percent tile, the difference in points (as a fraction)
    change_kind: ChangeKind
    direction: Direction | None
    good: bool | None


class TrendWeek(BaseModel):
    model_config = ConfigDict(frozen=True)

    week_start: date
    net_revenue: Decimal
    is_black_friday_week: bool
    is_partial: bool


def comparable(as_of: date, first_order_date: date) -> bool:
    """Whether the earlier 30 days all have data; otherwise the change would compare 30 days with fewer."""
    return first_order_date <= as_of - timedelta(days=EARLIER_WINDOW_START_DAYS)


def tile(query: TileQuery, this_period: Decimal | None, last_period: Decimal | None, *, comparable: bool) -> Tile:
    """A tile with its change. A change needs a value, a non-zero earlier value and a whole earlier window; without
    one the tile keeps its value and shows no change."""
    kind: ChangeKind = "points" if query.format == "percent" else "relative"
    change = _change(kind, this_period, last_period) if comparable else None
    direction = _direction(change)
    return Tile(
        id=query.id,
        title=query.title,
        format=query.format,
        decimals=query.decimals,
        up_is_good=query.up_is_good,
        note=query.note,
        value=this_period,
        change=change,
        change_kind=kind,
        direction=direction,
        good=None if direction in (None, "flat") else (direction == "up") == query.up_is_good,
    )


def trend_weeks(rows: Sequence[tuple[date, Decimal, bool]], as_of: date) -> tuple[TrendWeek, ...]:
    """The weeks as the page draws them; only the last one can be partial, when `as_of` is before its Sunday."""
    return tuple(
        TrendWeek(
            week_start=week_start,
            net_revenue=net_revenue,
            is_black_friday_week=is_black_friday_week,
            is_partial=as_of < week_start + WEEK_LAST_DAY_OFFSET,
        )
        for week_start, net_revenue, is_black_friday_week in rows
    )


def _change(kind: ChangeKind, this_period: Decimal | None, last_period: Decimal | None) -> Decimal | None:
    if this_period is None or not last_period:
        return None
    difference = this_period - last_period
    return difference if kind == "points" else difference / abs(last_period)


def _direction(change: Decimal | None) -> Direction | None:
    match change:
        case None:
            return None
        case _ if change > 0:
            return "up"
        case _ if change < 0:
            return "down"
        case _:
            return "flat"
