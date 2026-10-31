from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.realism_rules import RealismRules


class TestSimulationCalendar:
    def setup_method(self) -> None:
        self.calendar = SimulationCalendar()

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2025, 11, 26), 3.0),  # Black Friday week, Wednesday
            (date(2025, 11, 29), 3.6),  # Black Friday week x weekend
            (date(2025, 1, 15), 0.7),  # January, Wednesday
            (date(2025, 12, 6), 1.92),  # December x weekend
            (date(2025, 1, 4), 0.84),  # January x Saturday
            (date(2025, 10, 15), 1.0),  # plain weekday
            (date(2025, 10, 19), 1.2),  # plain Sunday
            (date(2025, 11, 23), 1.2),  # Sunday before the Black Friday week
            (date(2025, 11, 24), 3.0),  # first day of the week (Monday)
            (date(2025, 11, 30), 3.6),  # last day of the week (Sunday)
            (date(2025, 12, 1), 1.6),  # Monday after the week ends
            (date(2025, 2, 3), 1.0),  # February is untouched
            (date(2024, 11, 24), 1.2),  # Sunday before the 2024 week, which starts on the 25th
            (date(2024, 11, 29), 3.0),  # 2024 Black Friday (a 5th Friday)
            (date(2024, 11, 30), 3.6),  # Saturday of the 2024 week
            (date(2024, 12, 1), 1.6 * 1.2 * 3.0),  # Black Friday week x December x weekend
            (date(2024, 12, 2), 1.6),  # first December day after the week
        ],
    )
    def test_seasonality_examples(self, day: date, expected: float) -> None:
        assert self.calendar.seasonality(day) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("year", "expected"),
        [
            (2025, (date(2025, 11, 24), date(2025, 11, 30))),
            (2026, (date(2026, 11, 23), date(2026, 11, 29))),
            # 2024-11-01 is a Friday, so Thanksgiving is the 28th and the week crosses into December.
            (2024, (date(2024, 11, 25), date(2024, 12, 1))),
        ],
    )
    def test_black_friday_week_is_monday_to_sunday_of_the_day_after_thanksgiving(
        self, year: int, expected: tuple[date, date]
    ) -> None:
        assert self.calendar.black_friday_week(year) == expected

    def test_day_bounds_is_half_open_utc_day(self) -> None:
        start, end = self.calendar.day_bounds(date(2025, 3, 31))

        assert start == datetime(2025, 3, 31, tzinfo=UTC)
        assert end == datetime(2025, 4, 1, tzinfo=UTC)

    def test_local_iso_formats_with_offset_and_whole_seconds(self) -> None:
        instant = datetime(2025, 7, 1, 12, 3, 22, 999999, tzinfo=UTC)

        assert self.calendar.local_iso(instant, "DE") == "2025-07-01T14:03:22+02:00"

    @pytest.mark.parametrize(
        ("country", "winter", "summer"),
        [("DE", "+01:00", "+02:00"), ("GB", "+00:00", "+01:00")],
    )
    def test_local_iso_respects_dst(self, country: str, winter: str, summer: str) -> None:
        january = datetime(2025, 1, 15, 12, tzinfo=UTC)
        july = datetime(2025, 7, 15, 12, tzinfo=UTC)

        assert self.calendar.local_iso(january, country).endswith(winter)
        assert self.calendar.local_iso(july, country).endswith(summer)

    def test_local_iso_crosses_local_midnight(self) -> None:
        instant = datetime(2025, 7, 1, 22, 30, tzinfo=UTC)

        assert self.calendar.local_iso(instant, "ES") == "2025-07-02T00:30:00+02:00"

    def test_every_weighted_country_has_a_timezone(self) -> None:
        assert set(RealismRules.COUNTRY_WEIGHTS) == set(SimulationCalendar.TIMEZONES_BY_COUNTRY)
