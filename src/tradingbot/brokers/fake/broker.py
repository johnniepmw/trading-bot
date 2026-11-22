"""An in-memory broker for tests and paper runs. No more capable than the real API."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from tradingbot.brokers.port import (
    TERMINAL,
    AccountSnapshot,
    BrokerError,
    BrokerOrder,
    BrokerTimeoutError,
    OrderStatus,
)
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Position, Side
from tradingbot.domain.money import Currency, Money


class FakeBroker:
    """Fills market orders at the current quote, with no costs.

    Knobs for drills, each as awkward as the real broker can be:
    - `market_open = False`: market orders queue as PENDING until `open_market()`;
    - `partial_fill_next = f`: the next order fills a fraction f now, the rest at `open_market()`;
    - `fail_next`: the next `place` raises it before anything is accepted;
    - `timeout_after_accepting_next`: the next order is accepted, then BrokerTimeoutError;
    - `cancel(order_id)`: cancels a working order, keeping what already filled.
    """

    def __init__(self, cash: Money, quotes: Mapping[Instrument, Money]) -> None:
        if cash.currency is not Currency.GBP:
            raise ValueError("the fake account is in GBP")
        self._cash = cash.amount
        self.quotes = dict(quotes)
        self._held: dict[Instrument, Decimal] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self.now: Callable[[], datetime] = lambda: datetime.now(UTC)
        self.market_open = True
        self.partial_fill_next: Decimal | None = None
        self.fail_next: BrokerError | None = None
        self.timeout_after_accepting_next = False

    def account(self) -> AccountSnapshot:
        value = sum((q * self.quotes[i].to_gbp().amount for i, q in self._held.items()), Decimal(0))
        return AccountSnapshot(Money(self._cash, Currency.GBP), Money(self._cash + value, Currency.GBP))

    def positions(self) -> Sequence[Position]:
        return [Position(i, q) for i, q in sorted(self._held.items(), key=lambda x: x[0].ticker) if q > 0]

    def quote(self, instrument: Instrument) -> Money:
        if instrument not in self.quotes:
            raise BrokerError(f"no quote for {instrument.ticker}")
        return self.quotes[instrument]

    def place(self, order: ApprovedOrder) -> str:
        if not isinstance(order, ApprovedOrder):
            raise TypeError("the broker accepts only an ApprovedOrder")
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        order_id = str(uuid.uuid4())
        self._orders[order_id] = BrokerOrder(
            order_id, order.order, OrderStatus.PENDING, Decimal(0), self.now()
        )
        if self.market_open:
            fraction, self.partial_fill_next = self.partial_fill_next, None
            self._fill(order_id, order.order.quantity * (fraction if fraction is not None else 1))
        if self.timeout_after_accepting_next:
            self.timeout_after_accepting_next = False
            raise BrokerTimeoutError("timed out after the broker accepted the order")
        return order_id

    def _fill(self, order_id: str, quantity: Decimal) -> None:
        current = self._orders[order_id]
        o = current.order
        price = self.quote(o.instrument).to_gbp().amount
        held = self._held.get(o.instrument, Decimal(0))
        sign = 1 if o.side is Side.BUY else -1
        if (o.side is Side.BUY and quantity * price > self._cash) or (
            o.side is Side.SELL and quantity > held
        ):
            self._orders[order_id] = replace(current, status=OrderStatus.REJECTED)
            return
        self._cash -= sign * quantity * price
        self._held[o.instrument] = held + sign * quantity
        filled = current.filled_quantity + quantity
        status = OrderStatus.FILLED if filled == o.quantity else OrderStatus.PARTIALLY_FILLED
        self._orders[order_id] = replace(current, status=status, filled_quantity=filled)

    def open_market(self) -> None:
        """Fill every working order in full."""
        self.market_open = True
        for order_id, o in list(self._orders.items()):
            if o.status not in TERMINAL:
                self._fill(order_id, o.order.quantity - o.filled_quantity)

    def cancel(self, order_id: str) -> None:
        o = self.order(order_id)
        if o.status not in TERMINAL:
            self._orders[order_id] = replace(o, status=OrderStatus.CANCELLED)

    def order(self, order_id: str) -> BrokerOrder:
        if order_id not in self._orders:
            raise BrokerError(f"unknown order {order_id}")
        return self._orders[order_id]

    def recent_orders(self) -> Sequence[BrokerOrder]:
        return sorted(self._orders.values(), key=lambda o: o.created_at)
