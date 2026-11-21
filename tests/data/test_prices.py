from __future__ import annotations

from datetime import date

import pytest

from tests.bars import bars
from tradingbot.data.prices import DataError, validate_history
from tradingbot.domain.market import Bar, Instrument
from tradingbot.domain.money import Currency

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)


def test_a_clean_history_passes_unchanged() -> None:
    history = bars(VWRP, [100.0, 101.0, 99.5])
    assert validate_history(VWRP, history) == history


def test_bars_for_another_instrument_are_refused() -> None:
    with pytest.raises(DataError, match="SGLN"):
        validate_history(VWRP, bars(SGLN, [100.0]))


def test_days_must_strictly_increase() -> None:
    a, b = bars(VWRP, [100.0, 101.0])
    with pytest.raises(DataError, match="order"):
        validate_history(VWRP, [b, a])
    with pytest.raises(DataError, match="order"):
        validate_history(VWRP, [a, a])


@pytest.mark.parametrize("jump", [100.0, 0.01])  # pounds read as pence, or the reverse
def test_a_hundredfold_jump_is_refused(jump: float) -> None:
    history = bars(VWRP, [100.0, 100.0 * jump])
    with pytest.raises(DataError, match="jump"):
        validate_history(VWRP, history)


def test_a_large_but_plausible_move_passes() -> None:
    validate_history(VWRP, bars(VWRP, [100.0, 60.0, 100.0]))


def test_an_empty_history_is_valid() -> None:
    assert validate_history(VWRP, []) == []


def test_weekend_dates_are_not_checked_here() -> None:
    # Trading calendars belong to the data source; validation only checks order and scale.
    sat = Bar(VWRP, date(2026, 11, 21), open=1.0, close=1.0)
    assert validate_history(VWRP, [sat]) == [sat]


def test_an_open_far_from_its_own_close_is_refused() -> None:
    # backtest fills use the open, so a pence/pounds slip there matters too
    with pytest.raises(DataError, match="open"):
        validate_history(VWRP, [Bar(VWRP, date(2026, 11, 20), open=10_000.0, close=100.0)])
