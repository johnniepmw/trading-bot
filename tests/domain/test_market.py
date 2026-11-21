from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from tradingbot.domain.market import Bar, Instrument, Order, Position, Side
from tradingbot.domain.money import Currency

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)


def test_instrument_has_a_ticker_and_quote_currency() -> None:
    assert VWRP.ticker == "VWRP"
    assert SGLN.currency is Currency.GBX


@pytest.mark.parametrize("ticker", ["", " ", "vw rp"])
def test_instrument_rejects_blank_or_spaced_tickers(ticker: str) -> None:
    with pytest.raises(ValueError, match="ticker"):
        Instrument(ticker, Currency.GBP)


def test_instrument_currency_must_be_a_currency() -> None:
    with pytest.raises(TypeError, match="Currency"):
        Instrument("VWRP", "GBP")  # type: ignore[arg-type]


def test_bar_holds_a_day_with_open_and_close() -> None:
    bar = Bar(VWRP, date(2026, 11, 20), open=101.5, close=102.0)
    assert (bar.day, bar.open, bar.close) == (date(2026, 11, 20), 101.5, 102.0)


@pytest.mark.parametrize("price", [0.0, -1.0, math.nan, math.inf])
def test_bar_prices_must_be_positive_and_finite(price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        Bar(VWRP, date(2026, 11, 20), open=price, close=1.0)
    with pytest.raises(ValueError, match="price"):
        Bar(VWRP, date(2026, 11, 20), open=1.0, close=price)


def test_order_quantity_is_a_positive_decimal() -> None:
    order = Order(VWRP, Side.BUY, Decimal("0.5"))
    assert order.quantity == Decimal("0.5")
    for bad in (Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="quantity"):
            Order(VWRP, Side.SELL, bad)
    with pytest.raises(TypeError, match="Decimal"):
        Order(VWRP, Side.BUY, 0.5)  # type: ignore[arg-type]


def test_side_must_be_a_side() -> None:
    with pytest.raises(TypeError, match="Side"):
        Order(VWRP, "BUY", Decimal(1))  # type: ignore[arg-type]


def test_position_quantity_is_a_non_negative_decimal() -> None:
    assert Position(VWRP, Decimal(0)).quantity == Decimal(0)
    with pytest.raises(ValueError, match="quantity"):
        Position(VWRP, Decimal("-0.1"))  # long-or-cash: no shorts
    with pytest.raises(TypeError, match="Decimal"):
        Position(VWRP, 1)  # type: ignore[arg-type]
