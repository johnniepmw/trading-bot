"""Long-or-cash trend signal (ADR-0015, docs/strategies/trend-long-or-cash.md).

Pure: callers pass bars; nothing here reads data or the clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from tradingbot.domain.market import Bar, Instrument

SMA_DAYS = 210  # about 10 months of trading days, including the signal day


def in_trend(closes: Sequence[float]) -> bool:
    """True when the last close is above the simple average of the last SMA_DAYS closes."""
    if len(closes) < SMA_DAYS:
        return False
    window = closes[-SMA_DAYS:]
    return window[-1] > sum(window) / SMA_DAYS


def signals(history: Mapping[Instrument, Sequence[Bar]], as_of: date) -> dict[Instrument, bool]:
    """The trend signal for each instrument, using only bars on or before `as_of`."""
    return {
        instrument: in_trend([b.close for b in bars if b.day <= as_of])
        for instrument, bars in history.items()
    }


def month_end_days(days: Iterable[date]) -> set[date]:
    """The last trading day of each month that is complete in `days`.

    The final month is left out: without a later day we can't know it has ended.
    """
    last: dict[tuple[int, int], date] = {}
    for day in days:
        key = (day.year, day.month)
        last[key] = max(day, last.get(key, day))
    final = max(last, default=None)
    return {day for key, day in last.items() if key != final}
