"""Synthetic daily prices for local runs and staging (never prod: the job refuses it there).

Deterministic per instrument and date, so separate runs see the same history: a smooth
trend (up for about two in three instruments, down for the rest) plus a gentle wave. Weekdays only.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import date, timedelta

from tradingbot.domain.market import Bar, Instrument
from tradingbot.domain.money import Currency

_EPOCH = date(2015, 1, 1)


def _close(instrument: Instrument, day: date) -> float:
    seed = int.from_bytes(hashlib.sha256(instrument.ticker.encode()).digest()[:4], "big")
    drift = 0.0003 if seed % 3 else -0.0002  # about two in three trend up
    phase = seed % 100 / 10
    n = (day - _EPOCH).days
    base = 5000.0 if instrument.currency is Currency.GBX else 100.0
    return round(base * math.exp(drift * n + 0.03 * math.sin(n / 9 + phase)), 4)


def synthetic_close(instrument: Instrument, day: date) -> float:
    return _close(instrument, day)


class SyntheticHistory:
    def daily_bars(self, instrument: Instrument, start: date, end: date) -> Sequence[Bar]:
        bars = []
        day = start
        while day <= end:
            if day.weekday() < 5:  # noqa: PLR2004 - weekdays
                close = _close(instrument, day)
                bars.append(Bar(instrument, day, open=round(close * 0.999, 4), close=close))
            day += timedelta(days=1)
        return bars
