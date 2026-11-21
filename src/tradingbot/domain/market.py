"""Instruments, daily bars, orders and positions (ADR-0010, ADR-0016).

Prices in bars are floats: they feed statistics (returns, averages, volatility).
Quantities are Decimals: they become orders and must not drift. Long-or-cash only, so
positions are never negative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from tradingbot.domain.money import Currency


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
        for name, price in (("open", self.open), ("close", self.close)):
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"bar {name} price must be positive and finite, got {price}")


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
