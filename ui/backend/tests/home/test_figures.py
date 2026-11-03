from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sherpa_ui.home.figures import TrendWeek, comparable, tile, trend_weeks
from sherpa_ui.home.home_queries import TileQuery


def make_query(**overrides: object) -> TileQuery:
    fields: dict[str, object] = {
        "id": "orders",
        "title": "Orders",
        "format": "count",
        "decimals": 0,
        "up_is_good": True,
        "note": None,
        "sql": "select 1",
    }
    return TileQuery.model_validate({**fields, **overrides})


class TestTile:
    def test_relative_change_up_is_good(self) -> None:
        result = tile(make_query(), Decimal(110), Decimal(100), comparable=True)

        assert result.value == Decimal(110)
        assert result.change == Decimal("0.1")
        assert result.change_kind == "relative"
        assert result.direction == "up"
        assert result.good is True

    def test_points_change_down_is_good_when_up_is_bad(self) -> None:
        query = make_query(id="refund_rate", format="percent", decimals=1, up_is_good=False)

        result = tile(query, Decimal("0.0917"), Decimal("0.0984"), comparable=True)

        assert result.change == Decimal("-0.0067")
        assert result.change_kind == "points"
        assert result.direction == "down"
        assert result.good is True

    def test_points_change_up_is_bad_when_up_is_bad(self) -> None:
        query = make_query(format="percent", up_is_good=False)

        result = tile(query, Decimal("0.10"), Decimal("0.08"), comparable=True)

        assert result.direction == "up"
        assert result.good is False

    @pytest.mark.parametrize(
        ("last", "is_comparable"),
        [(Decimal(0), True), (None, True), (Decimal(100), False)],
        ids=["zero earlier value", "no earlier value", "history too short"],
    )
    def test_no_change_keeps_the_value(self, last: Decimal | None, is_comparable: bool) -> None:
        result = tile(make_query(), Decimal(110), last, comparable=is_comparable)

        assert result.value == Decimal(110)
        assert result.change is None
        assert result.direction is None
        assert result.good is None

    def test_no_value_has_no_change(self) -> None:
        result = tile(make_query(id="aov"), None, Decimal(100), comparable=True)

        assert result.value is None
        assert result.change is None

    def test_equal_values_are_flat(self) -> None:
        result = tile(make_query(), Decimal(100), Decimal(100), comparable=True)

        assert result.change == Decimal(0)
        assert result.direction == "flat"
        assert result.good is None

    def test_negative_base_follows_this_minus_last(self) -> None:
        result = tile(make_query(), Decimal(-50), Decimal(-100), comparable=True)

        assert result.change == Decimal("0.5")
        assert result.direction == "up"


def test_change_needs_the_whole_earlier_window() -> None:
    assert comparable(date(2026, 10, 31), date(2026, 9, 2)) is True
    assert comparable(date(2026, 10, 31), date(2026, 9, 3)) is False


def test_last_week_is_partial_unless_as_of_is_its_sunday() -> None:
    rows = [
        (date(2026, 10, 19), Decimal(100), False),
        (date(2026, 10, 26), Decimal(200), True),
    ]

    sunday = trend_weeks(rows, date(2026, 11, 1))
    saturday = trend_weeks(rows, date(2026, 10, 31))

    assert [week.is_partial for week in sunday] == [False, False]
    assert [week.is_partial for week in saturday] == [False, True]
    assert sunday[1] == TrendWeek(
        week_start=date(2026, 10, 26), net_revenue=Decimal(200), is_black_friday_week=True, is_partial=False
    )


def test_no_trend_rows_give_no_weeks() -> None:
    assert trend_weeks([], date(2026, 11, 1)) == ()
