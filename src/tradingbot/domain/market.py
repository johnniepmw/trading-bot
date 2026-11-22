"""Instruments, daily bars, orders and positions (ADR-0010, ADR-0016).

Prices in bars are floats: they feed statistics (returns, averages, volatility).
`price_money` is the only place a float price becomes Money.
Quantities are Decimals: they become orders and must not drift. Long-or-cash only, so
positions are never negative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from tradingbot.domain.money import Currency, Money


@dataclass(frozen=True, slots=True)
class Instrument:
    ticker: str
    currency: Currency  # quote currency; London ETFs may quote in GBX (pence)

    def __post_init__(self) -> None:
        if not self.ticker.strip() or any(c.isspace() for c in self.ticker):
            raise ValueError(f"instrument ticker must be non-blank with no spaces, got {self.ticker!r}")
        if not isinstance(self.currency, Currency):
            raise TypeError(f"instrument currency must be a Currency, got {self.currency!r}")


@dataclass(frozen=True, slots=True)
class Bar:
    """One trading day's prices for an instrument, in its quote currency."""

    instrument: Instrument
    day: date
    open: float
    close: float

    def __post_init__(self) -> None:
        _check_price("bar open", self.open)
        _check_price("bar close", self.close)


def _check_price(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} price must be positive and finite, got {value}")


def price_money(instrument: Instrument, value: float) -> Money:
    """The one conversion from a float price to Money, in the instrument's quote currency.

    Uses the float's shortest repr, so 0.1 becomes Decimal("0.1"). A GBX price stays GBX.
    """
    _check_price("instrument", value)
    return Money(
        Decimal(repr(float(value))), instrument.currency
    )  # float(): numpy scalars repr as np.float64(...)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def _decimal(name: str, value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Order:
    """A proposed market order. It reaches a broker only as an ApprovedOrder."""

    instrument: Instrument
    side: Side
    quantity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.side, Side):
            raise TypeError(f"order side must be a Side, got {self.side!r}")
        if _decimal("order quantity", self.quantity) <= 0:
            raise ValueError(f"order quantity must be positive, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class Position:
    instrument: Instrument
    quantity: Decimal

    def __post_init__(self) -> None:
        if _decimal("position quantity", self.quantity) < 0:
            raise ValueError(f"position quantity must not be negative, got {self.quantity}")


BENCHMARK_ONLY = frozenset({"SSAC"})  # monitoring spec: compared against, never traded


def base_ticker(ticker: str) -> str:
    """The exchange code without data-source or broker decoration: "SSAC.L", "ssac" and a
    Trading 212 style "SSACl_EQ" are all "SSAC"."""
    code = ticker.split("_", maxsplit=1)[0].split(".", maxsplit=1)[0]
    if len(code) > 1 and code[-1] == "l" and code[:-1].isupper():
        code = code[:-1]  # Trading 212 marks London listings with a lower-case "l"
    return code.upper()


def is_benchmark_only(ticker: str) -> bool:
    return base_ticker(ticker) in BENCHMARK_ONLY
