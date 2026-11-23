"""An in-memory broker for tests and paper runs. No more capable than the real API."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tradingbot.brokers.port import (
    TERMINAL,
    AccountSnapshot,
    BrokerError,
    BrokerOrder,
    BrokerTimeoutError,
    OrderStatus,
)
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money


def _average(raw: object, instrument: Instrument) -> Money | None:
    return None if raw is None else Money(Decimal(str(raw)), instrument.currency)


class FakeBroker:
    """Fills market orders at the current quote, with no costs (fees reported as £0).

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
        self.after_place: Callable[[], None] | None = None  # e.g. save the account (staging)

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
        try:
            return self._place(order)
        finally:
            if self.after_place is not None:
                self.after_place()

    def _place(self, order: ApprovedOrder) -> str:
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
        price = FakeBroker.quote(self, o.instrument).to_gbp().amount  # fills at the market price, held or not
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
        if quantity == 0:  # accepted, nothing filled yet
            return
        quote = FakeBroker.quote(self, o.instrument)
        before = (
            current.average_price.amount * current.filled_quantity if current.average_price else Decimal(0)
        )
        average = Money((before + quote.amount * quantity) / filled, quote.currency)
        self._orders[order_id] = replace(
            current,
            status=status,
            filled_quantity=filled,
            average_price=average,
            fees=Money(Decimal(0), Currency.GBP),
        )

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

    # --- persistence for local and staging runs on fakes (Phase 2 step 2b) -------------
    def dump(self) -> dict[str, object]:
        """The account's state as JSON-ready data (quotes are not state: they come from prices)."""
        return {
            "cash": str(self._cash),
            "held": [[i.ticker, i.currency.value, str(q)] for i, q in self._held.items()],
            "orders": [
                {
                    "id": o.order_id,
                    "ticker": o.order.instrument.ticker,
                    "currency": o.order.instrument.currency.value,
                    "side": o.order.side.value,
                    "quantity": str(o.order.quantity),
                    "status": o.status.value,
                    "filled": str(o.filled_quantity),
                    "created_at": o.created_at.isoformat(),
                    "average_price": None if o.average_price is None else str(o.average_price.amount),
                }
                for o in self._orders.values()
            ],
        }

    @classmethod
    def load(cls, state: Mapping[str, Any], quotes: Mapping[Instrument, Money]) -> FakeBroker:
        """The inverse of `dump`. `Any`: parsed JSON from our own staging bucket."""
        broker = cls(Money(Decimal(state["cash"]), Currency.GBP), quotes)
        for ticker, currency, quantity in state["held"]:
            broker._held[Instrument(ticker, Currency(currency))] = Decimal(quantity)
        for raw in state["orders"]:
            order = Order(
                Instrument(raw["ticker"], Currency(raw["currency"])),
                Side(raw["side"]),
                Decimal(raw["quantity"]),
            )
            broker._orders[raw["id"]] = BrokerOrder(
                raw["id"],
                order,
                OrderStatus(raw["status"]),
                Decimal(raw["filled"]),
                datetime.fromisoformat(raw["created_at"]),
                average_price=_average(raw.get("average_price"), order.instrument),
                fees=None if raw.get("average_price") is None else Money(Decimal(0), Currency.GBP),
            )
        return broker
