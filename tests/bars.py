"""Synthetic daily bars for tests (no real market data in the repo, ADR-0014)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from tradingbot.domain.market import Bar, Instrument


def weekdays(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def bars(instrument: Instrument, closes: Sequence[float], start: date = date(2020, 1, 1)) -> list[Bar]:
    return [
        Bar(instrument, d, open=c, close=c) for d, c in zip(weekdays(start, len(closes)), closes, strict=True)
    ]
