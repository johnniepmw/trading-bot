"""A frozen, seeded synthetic market for backtest tests (no real data in the repo)."""

from __future__ import annotations

import random
from datetime import date

from tests.bars import weekdays
from tradingbot.domain.market import Bar, Instrument
from tradingbot.domain.money import Currency

UNIVERSE = [
    Instrument("EQ", Currency.GBP),  # trends up
    Instrument("BD", Currency.GBX),  # pence, drifts down then up
    Instrument("GD", Currency.GBP),  # choppy
    Instrument("EM", Currency.GBP),  # crashes mid-way
]


def market(days: int = 750, seed: int = 7) -> dict[Instrument, list[Bar]]:
    rng = random.Random(seed)  # noqa: S311 - synthetic prices, not security
    calendar = weekdays(date(2018, 1, 1), days)
    drift = {
        "EQ": lambda t: 0.0006,
        "BD": lambda t: -0.0004 if t < days // 2 else 0.0005,
        "GD": lambda t: 0.0,
        "EM": lambda t: 0.0008 if t < days // 2 else -0.0015,
    }
    start = {"EQ": 100.0, "BD": 4000.0, "GD": 50.0, "EM": 20.0}
    history: dict[Instrument, list[Bar]] = {}
    for instrument in UNIVERSE:
        close = start[instrument.ticker]
        bars = []
        for t, day in enumerate(calendar):
            open_ = close * (1 + rng.gauss(0, 0.002))
            close = open_ * (1 + drift[instrument.ticker](t) + rng.gauss(0, 0.01))
            bars.append(Bar(instrument, day, open=round(open_, 4), close=round(close, 4)))
        history[instrument] = bars
    return history
