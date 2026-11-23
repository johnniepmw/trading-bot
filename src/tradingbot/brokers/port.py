"""The broker interface (ADR-0008, ADR-0016).

Deliberately no richer than Trading 212's public API as far as we know it before the
owner's API check: market orders, broker-assigned order IDs, no client order IDs. So
idempotency lives in the ledger (record intent, then send, then reconcile), not in the
broker. Price history is not here (`data.prices.PriceHistory`); `quote` is the live
price used for sizing.

`quote` for an instrument not held may raise `NoQuoteError`: Trading 212's public API shows
prices only for held positions (owner's API check, 2026-11-23), so the cycle sizes a first
buy from the data source's latest close (Phase 1 open question 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position
from tradingbot.domain.money import Money


class BrokerError(Exception):
    """The broker refused a request and nothing was placed. Adapters raise this only when
    that is certain (e.g. a 4xx validation error); anything ambiguous is a timeout."""


class BrokerTimeoutError(BrokerError):
    """The outcome is unknown: no answer in time, a connection reset after sending, a 5xx
    from a gateway. The request may or may not have taken effect."""


class NoQuoteError(BrokerError):
    """The broker has no live price for this instrument (Trading 212 shows prices only for
    held positions). The cycle then sizes a first buy from the data source's latest close."""


class OrderStatus(StrEnum):
    PENDING = "PENDING"  # working, nothing filled (e.g. queued while the market is closed)
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # working: the rest may still fill
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # finished; `filled_quantity` says how much filled first


TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED})


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
    created_at: datetime  # UTC, as the broker records it


class BrokerReader(Protocol):
    """Everything but sending: what a dry run gets, so it can't place an order (Phase 2 AC6)."""

    def account(self) -> AccountSnapshot: ...

    def positions(self) -> Sequence[Position]:
        """Open positions, mapped to our instruments. An unmappable position is a BrokerError."""
        ...

    def quote(self, instrument: Instrument) -> Money:
        """The live price in the instrument's quote currency."""
        ...

    def order(self, order_id: str) -> BrokerOrder: ...

    def recent_orders(self) -> Sequence[BrokerOrder]:
        """Every order since the oldest open ledger intent (at least the last 7 days),
        working ones included, oldest first. Reconcile relies on this being complete."""
        ...


class Broker(BrokerReader, Protocol):
    def place(self, order: ApprovedOrder) -> str:
        """Send a market order; return the broker's order ID. Raises BrokerTimeoutError when the
        outcome is unknown, BrokerError when nothing was placed."""
        ...


class _ReadOnly:
    def __init__(self, broker: BrokerReader) -> None:
        self._broker = broker

    def account(self) -> AccountSnapshot:
        return self._broker.account()

    def positions(self) -> Sequence[Position]:
        return self._broker.positions()

    def quote(self, instrument: Instrument) -> Money:
        return self._broker.quote(instrument)

    def order(self, order_id: str) -> BrokerOrder:
        return self._broker.order(order_id)

    def recent_orders(self) -> Sequence[BrokerOrder]:
        return self._broker.recent_orders()


def read_only(broker: BrokerReader) -> BrokerReader:
    """A view with no `place` at all, so a dry run can't send even by going round the types."""
    return _ReadOnly(broker)
