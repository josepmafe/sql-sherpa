from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

FRIDAY = 4  # date.weekday() value
WEEKEND_START = 5  # date.weekday() of Saturday; Sunday is 6


class SimulationCalendar:
    """Day-level seasonality and time-zone handling (Spec A §4.4 baseline distributions)."""

    WEEKEND_FACTOR: Final = 1.2
    BLACK_FRIDAY_WEEK_FACTOR: Final = 3.0
    DECEMBER_FACTOR: Final = 1.6
    JANUARY_FACTOR: Final = 0.7
    NOVEMBER: Final = 11
    DECEMBER: Final = 12
    JANUARY: Final = 1
    BLACK_FRIDAY_ORDINAL: Final = 4  # the 4th Friday of November
    DAYS_PER_WEEK: Final = 7

    TIMEZONES_BY_COUNTRY: Final = {
        "DE": "Europe/Berlin",
        "FR": "Europe/Paris",
        "ES": "Europe/Madrid",
        "IT": "Europe/Rome",
        "NL": "Europe/Amsterdam",
        "GB": "Europe/London",
        "AT": "Europe/Vienna",
        "BE": "Europe/Brussels",
    }

    def seasonality(self, day: date) -> float:
        """Volume multiplier for a day; the weekend, Black Friday week and month factors multiply."""
        factor = 1.0
        if day.weekday() >= WEEKEND_START:
            factor *= self.WEEKEND_FACTOR
        first, last = self.black_friday_week(day.year)
        if first <= day <= last:
            factor *= self.BLACK_FRIDAY_WEEK_FACTOR
        if day.month == self.DECEMBER:
            factor *= self.DECEMBER_FACTOR
        if day.month == self.JANUARY:
            factor *= self.JANUARY_FACTOR
        return factor

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        """The UTC half-open interval [start, end) of a simulated day."""
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return start, start + timedelta(days=1)

    def black_friday_week(self, year: int) -> tuple[date, date]:
        """Monday to Sunday of the week containing the 4th Friday of November."""
        november_1st = date(year, self.NOVEMBER, 1)
        first_friday = november_1st + timedelta(days=(FRIDAY - november_1st.weekday()) % self.DAYS_PER_WEEK)
        black_friday = first_friday + timedelta(weeks=self.BLACK_FRIDAY_ORDINAL - 1)
        monday = black_friday - timedelta(days=FRIDAY)
        return monday, monday + timedelta(days=self.DAYS_PER_WEEK - 1)

    def local_iso(self, instant: datetime, country: str) -> str:
        """ISO-8601 string in the country's local time with its UTC offset, as a client would report it."""
        local = instant.astimezone(ZoneInfo(self.TIMEZONES_BY_COUNTRY[country]))
        return local.isoformat(timespec="seconds")
