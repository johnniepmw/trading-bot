"""The trading calendar picks the signal day, not the price data (Phase 2 AC5)."""

from __future__ import annotations

from datetime import date

import pytest

from tradingbot.app.calendar import CalendarError, TradingCalendar
from tradingbot.config.load import load_config

# London Stock Exchange closures, 2026 (as in the bundled config)
HOLIDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 4),
        date(2026, 5, 25),
        date(2026, 8, 31),
        date(2026, 12, 25),
        date(2026, 12, 28),
    }
)
CAL = TradingCalendar(HOLIDAYS, until=date(2026, 12, 31))


@pytest.mark.parametrize(
    ("day", "trading"),
    [
        (date(2026, 11, 30), True),  # a Monday
        (date(2026, 11, 28), False),  # a Saturday
        (date(2026, 12, 25), False),  # Christmas
        (date(2026, 8, 31), False),  # bank holiday
    ],
)
def test_trading_days_are_weekdays_that_arent_holidays(day: date, trading: bool) -> None:
    assert CAL.is_trading_day(day) is trading


@pytest.mark.parametrize(
    ("today", "signal_day"),
    [
        (date(2026, 12, 1), date(2026, 11, 30)),  # the first run after a month end
        (date(2026, 11, 30), date(2026, 10, 30)),  # on the month's last day its close isn't in yet
        (date(2026, 9, 1), date(2026, 8, 28)),  # August ends on a bank holiday Monday
        (date(2026, 11, 2), date(2026, 10, 30)),  # October ends on a Saturday
        (date(2026, 10, 31), date(2026, 10, 30)),  # a weekend run after the month's last trading day
    ],
)
def test_the_signal_day_is_the_latest_month_end_trading_day_before_today(
    today: date, signal_day: date
) -> None:
    assert CAL.signal_day(today) == signal_day


def test_a_late_month_end_bar_doesnt_shift_the_signal_day() -> None:
    # the calendar never looks at prices: if 30 November's bar is missing on 1 December the
    # signal day is still 30 November, and the cycle's data check stops the run
    assert CAL.signal_day(date(2026, 12, 1)) == date(2026, 11, 30)


def test_dates_beyond_the_calendar_are_refused() -> None:
    with pytest.raises(CalendarError, match="2027-01-04"):
        CAL.is_trading_day(date(2027, 1, 4))


def test_a_signal_day_needs_the_calendar_to_cover_today() -> None:
    with pytest.raises(CalendarError):
        CAL.signal_day(date(2027, 1, 4))


BUNDLED = TradingCalendar(load_config().holidays, until=load_config().calendar_until)


@pytest.mark.parametrize(
    ("today", "signal_day"),
    [
        (date(2027, 1, 4), date(2026, 12, 31)),  # across the year boundary
        (date(2027, 1, 1), date(2026, 12, 31)),  # New Year's Day itself
        (date(2027, 9, 1), date(2027, 8, 31)),  # the day after a month ending just after a bank holiday
    ],
)
def test_the_bundled_calendar_picks_the_signal_day(today: date, signal_day: date) -> None:
    assert BUNDLED.signal_day(today) == signal_day


def test_the_bundled_calendar_refuses_2028() -> None:
    with pytest.raises(CalendarError):
        BUNDLED.signal_day(date(2028, 1, 4))
