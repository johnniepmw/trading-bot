from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.bars import bars, weekdays
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency
from tradingbot.strategy.trend import SMA_DAYS, in_trend, month_end_days, signals

VWRP = Instrument("VWRP", Currency.GBP)
IGLT = Instrument("IGLT", Currency.GBX)


def test_the_average_is_210_trading_days() -> None:
    assert SMA_DAYS == 210


def test_in_trend_when_the_close_is_above_the_average_including_today() -> None:
    closes = [100.0] * 209 + [101.0]  # average 100.0047..; 101 is above
    assert in_trend(closes) is True


def test_not_in_trend_at_or_below_the_average() -> None:
    assert in_trend([100.0] * 210) is False  # equal is not above
    assert in_trend([100.0] * 209 + [99.0]) is False


def test_only_the_last_210_closes_count() -> None:
    # A huge old close outside the window must not drag the average up.
    assert in_trend([10_000.0] + [100.0] * 209 + [101.0]) is True


def test_fewer_than_210_closes_means_not_held() -> None:
    assert in_trend([100.0] * 208 + [200.0]) is False


def test_signals_use_only_bars_up_to_the_signal_date() -> None:
    history = bars(VWRP, [100.0] * 209 + [101.0] + [50.0])  # the crash comes the day after
    signal_day = history[209].day
    assert signals({VWRP: history}, signal_day) == {VWRP: True}
    assert signals({VWRP: history}, history[210].day) == {VWRP: False}


def test_signals_cover_every_instrument_given() -> None:
    up = bars(VWRP, [100.0] * 209 + [101.0])
    short = bars(IGLT, [100.0] * 20)
    assert signals({VWRP: up, IGLT: short}, up[-1].day) == {VWRP: True, IGLT: False}


def test_month_end_days_are_the_last_trading_day_of_each_completed_month() -> None:
    days = weekdays(date(2026, 1, 26), 30)  # late Jan to early Mar
    # March isn't over in this data, so its last day so far is not a month end
    assert month_end_days(days) == {date(2026, 1, 30), date(2026, 2, 27)}


@given(
    st.lists(st.floats(min_value=1.0, max_value=1000.0), min_size=211, max_size=300), st.floats(1.0, 1000.0)
)
def test_a_later_bar_never_changes_an_earlier_signal(closes: list[float], later: float) -> None:
    history = bars(VWRP, closes)
    extended = bars(VWRP, [*closes, later])
    day = history[-1].day
    assert signals({VWRP: history}, day) == signals({VWRP: extended}, day)


def test_signals_do_not_depend_on_bar_order() -> None:
    history = bars(VWRP, [100.0] * 209 + [101.0])
    assert signals({VWRP: list(reversed(history))}, history[-1].day) == {VWRP: True}


@pytest.mark.parametrize("bad", [0, 1, -5])
def test_the_average_window_must_be_at_least_two_days(bad: int) -> None:
    with pytest.raises(ValueError, match="days"):
        in_trend([100.0] * 300, bad)
