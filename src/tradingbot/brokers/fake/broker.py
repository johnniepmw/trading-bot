"""An in-memory broker for tests and paper runs. No more capable than the real API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from tradingbot.brokers.port import AccountSnapshot, BrokerError, BrokerOrder, BrokerTimeoutError, OrderStatus
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Position, Side
from tradingbot.domain.money import Currency, Money


class FakeBroker:
    """Fills market orders at the current quote, in full, with no costs.

    Failure injection for drills: `fail_next` raises before the order is accepted;
    `timeout_after_accepting_next` accepts and fills the order, then raises BrokerTimeoutError.
    """

    def __init__(self, cash: Money, quotes: Mapping[Instrument, Money]) -> None:
        if cash.currency is not Currency.GBP:
            raise ValueError("the fake account is in GBP")
        self._cash = cash.amount
        self.quotes = dict(quotes)
        self._held: dict[Instrument, Decimal] = {}
        self._orders: list[BrokerOrder] = []
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
        o = order.order
        price = self.quote(o.instrument).to_gbp().amount
        held = self._held.get(o.instrument, Decimal(0))
        affordable = o.side is Side.SELL or o.quantity * price <= self._cash
        status = (
            OrderStatus.FILLED
            if affordable and (o.side is Side.BUY or o.quantity <= held)
            else OrderStatus.REJECTED
        )
        if status is OrderStatus.FILLED:
            sign = 1 if o.side is Side.BUY else -1
            self._cash -= sign * o.quantity * price
            self._held[o.instrument] = held + sign * o.quantity
        order_id = f"fake-{len(self._orders) + 1}"
        filled = o.quantity if status is OrderStatus.FILLED else Decimal(0)
        self._orders.append(BrokerOrder(order_id, o, status, filled))
        if self.timeout_after_accepting_next:
            self.timeout_after_accepting_next = False
            raise BrokerTimeoutError("timed out after the broker accepted the order")
        return order_id

    def order(self, order_id: str) -> BrokerOrder:
        for o in self._orders:
            if o.order_id == order_id:
                return o
        raise BrokerError(f"unknown order {order_id}")

    def recent_orders(self) -> Sequence[BrokerOrder]:
        return list(self._orders)
