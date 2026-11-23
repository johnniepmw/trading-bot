"""The trading calendar: weekdays minus the London Stock Exchange's holidays (Phase 2 AC5).

The signal day comes from here, never from the price data, so a late month-end bar is a
"stale data" stop rather than a shifted signal day. Dates past the configured list are
refused: an out-of-date calendar stops the bot instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


class CalendarError(Exception):
    """The calendar doesn't cover the date: update the holidays in the config file."""


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date]
    until: date

    def is_trading_day(self, day: date) -> bool:
        if day > self.until:
            raise CalendarError(
                f"{day} is past the calendar's end ({self.until}): add the next year's holidays"
            )
        return day.weekday() < 5 and day not in self.holidays  # noqa: PLR2004 - Monday to Friday

    def _last_trading_day_of_month(self, year: int, month: int) -> date:
        first_of_next = date(year + month // 12, month % 12 + 1, 1)
        day = first_of_next - timedelta(days=1)
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def next_month_end(self, today: date) -> date:
        """The month's last trading day, or next month's once today is past it."""
        this_month = self._last_trading_day_of_month(today.year, today.month)
        if this_month >= today:
            return this_month
        following = date(today.year + today.month // 12, today.month % 12 + 1, 1)
        return self._last_trading_day_of_month(following.year, following.month)

    def signal_day(self, today: date) -> date:
        """The latest month-end trading day strictly before `today` (its close is in)."""
        self.is_trading_day(today)  # refuses a date past the calendar
        this_month = self._last_trading_day_of_month(today.year, today.month)
        if this_month < today:
            return this_month
        previous = today.replace(day=1) - timedelta(days=1)
        return self._last_trading_day_of_month(previous.year, previous.month)
