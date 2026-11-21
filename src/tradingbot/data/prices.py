"""Price history: the port the job and backtests read daily bars through (ADR-0016).

`PriceHistory` is implemented by a data adapter wired in `app` (Phase 2) and by fakes in
tests. It is deliberately not part of `Broker`: Trading 212's API has no price history.
`validate_history` is the boundary check every adapter's output goes through.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from tradingbot.domain.market import Bar, Instrument

# A day-over-day move beyond 10x either way is a data error (typically pounds read as
# pence), not a market move. Real ETF moves are far smaller.
MAX_DAILY_RATIO = 10.0


class DataError(ValueError):
    """Price data that must not be traded on."""


class PriceHistory(Protocol):
    def daily_bars(self, instrument: Instrument, start: date, end: date) -> Sequence[Bar]:
        """Adjusted (total-return) daily bars from start to end inclusive, oldest first."""
        ...


def validate_history(instrument: Instrument, bars: Sequence[Bar]) -> Sequence[Bar]:
    """Return the bars unchanged if they belong to the instrument, are in strict date order
    and have no implausible jumps; raise DataError otherwise."""
    for bar in bars:
        if bar.instrument != instrument:
            raise DataError(f"bar for {bar.instrument.ticker} in the history of {instrument.ticker}")
        _check_ratio(instrument, bar.open / bar.close, f"open against close on {bar.day}")
    for before, after in itertools.pairwise(bars):
        if after.day <= before.day:
            raise DataError(f"{instrument.ticker}: bars out of order at {after.day}")
        _check_ratio(instrument, after.close / before.close, f"close on {after.day}")
    return bars


def _check_ratio(instrument: Instrument, ratio: float, what: str) -> None:
    if ratio > MAX_DAILY_RATIO or ratio < 1 / MAX_DAILY_RATIO:
        raise DataError(f"{instrument.ticker}: implausible jump of {ratio:.2f}x, {what}")
