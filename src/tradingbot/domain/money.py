"""Money: a Decimal amount with an explicit currency (ADR-0010).

London ETFs quote in pence (GBX) while the account is in pounds (GBP). Mixing them is a
100x error, so arithmetic and ordering across currencies raise instead of guessing.
Conversion from pence to pounds is explicit (`to_gbp`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

PENCE_PER_POUND = Decimal(100)


class Currency(StrEnum):
    GBP = "GBP"
    GBX = "GBX"  # pence sterling, as quoted on the London Stock Exchange
    USD = "USD"


class CurrencyMismatchError(ValueError):
    """Two amounts in different currencies were combined or compared."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money amount must be a Decimal, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise ValueError(f"Money amount must be finite, got {self.amount}")

    def _same(self, other: Money) -> Money:
        if self.currency is not other.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")
        return other

    def __add__(self, other: Money) -> Money:
        return Money(self.amount + self._same(other).amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount - self._same(other).amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal) -> Money:
        if not isinstance(factor, Decimal):
            raise TypeError(f"Money can only be scaled by a Decimal, got {type(factor).__name__}")
        return Money(self.amount * factor, self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._same(other).amount

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._same(other).amount

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._same(other).amount

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._same(other).amount

    def to_gbp(self) -> Money:
        """Pounds from pounds or pence. Other currencies need an explicit FX rate."""
        if self.currency is Currency.GBP:
            return self
        if self.currency is Currency.GBX:
            return Money(self.amount / PENCE_PER_POUND, Currency.GBP)
        raise CurrencyMismatchError(f"no implicit conversion from {self.currency} to GBP")
