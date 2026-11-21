"""The broker interface (ADR-0008, ADR-0016).

Deliberately no richer than Trading 212's public API as far as we know it before the
owner's API check: market orders, broker-assigned order IDs, no client order IDs. So
idempotency lives in the ledger (record intent, then send, then reconcile), not in the
broker. Price history is not here (`data.prices.PriceHistory`); `quote` is the live
price used for sizing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position
from tradingbot.domain.money import Money


class BrokerError(Exception):
    """The broker refused or failed a request; nothing was placed."""


class BrokerTimeoutError(BrokerError):
    """No answer in time: the request may or may not have taken effect."""


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    cash: Money  # GBP, available to trade
    equity: Money  # GBP, cash plus positions at current prices


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    order_id: str  # assigned by the broker
    order: Order
    status: OrderStatus
    filled_quantity: Decimal


class Broker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def positions(self) -> Sequence[Position]:
        """Open positions, mapped to our instruments. An unmappable position is a BrokerError."""
        ...

    def quote(self, instrument: Instrument) -> Money:
        """The live price in the instrument's quote currency."""
        ...

    def place(self, order: ApprovedOrder) -> str:
        """Send a market order; return the broker's order ID. Raises BrokerTimeoutError when the
        outcome is unknown, BrokerError when nothing was placed."""
        ...

    def order(self, order_id: str) -> BrokerOrder: ...

    def recent_orders(self) -> Sequence[BrokerOrder]:
        """Orders the broker received recently, oldest first (used to reconcile)."""
        ...
