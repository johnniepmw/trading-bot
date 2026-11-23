"""The Trading 212 adapter: the broker port over the public API, demo only (Phase 2 step 4).

Shapes and units from the owner's demo runs on 2026-11-23 (`scripts/t212_record.py`):
- `/equity/account/summary`: `currency`, `totalValue`, `cash.availableToTrade`;
- `/equity/positions`: `instrument.{ticker,currency}`, `quantity`, `currentPrice` in the
  instrument's currency (pence for GBX funds, as fills are);
- market orders: `{"ticker", "quantity" (negative to sell)}`, at most 3 decimal places
  (more is refused as `quantity-precision-mismatch`); the reply is the order, status NEW;
- `/equity/orders/{id}` answers only while an order is working; after that it is in the
  history (`history.py`).
Trading 212 prices only held positions, so `quote` for anything else is a `NoQuoteError`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal

from tradingbot.brokers.port import (
    AccountSnapshot,
    BrokerError,
    BrokerOrder,
    BrokerTimeoutError,
    NoQuoteError,
)
from tradingbot.brokers.trading212.client import NotFoundError, Trading212Client
from tradingbot.brokers.trading212.history import find_order, recent_orders
from tradingbot.brokers.trading212.orders import Tickers, decimal, parse_order
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Position, Side
from tradingbot.domain.money import Currency, Money

STEP = Decimal("0.001")  # finer quantities are refused (owner's demo run, 2026-11-23)


def _object(raw: object, what: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise BrokerError(f"Trading 212 sent {what} that is not an object")
    return raw


class Trading212Broker:
    def __init__(
        self, client: Trading212Client, universe: frozenset[Instrument], clock: Callable[[], datetime]
    ) -> None:
        self._client = client
        self._tickers = Tickers(universe)
        self._clock = clock

    def account(self) -> AccountSnapshot:
        summary = _object(self._client.get("/equity/account/summary", "summary"), "an account summary")
        if summary.get("currency") != "GBP":
            raise BrokerError("the Trading 212 account is not in GBP")
        cash = _object(summary.get("cash"), "a cash summary")
        return AccountSnapshot(
            cash=Money(decimal(cash, "availableToTrade"), Currency.GBP),
            equity=Money(decimal(summary, "totalValue"), Currency.GBP),
        )

    def _positions(self) -> list[tuple[Instrument, dict[str, object]]]:
        raw = self._client.get("/equity/positions", "positions")
        if not isinstance(raw, list):
            raise BrokerError("Trading 212 sent positions that are not a list")
        found = []
        for item in raw:
            position = _object(item, "a position")
            listing = _object(position.get("instrument"), "a position's instrument")
            instrument = self._tickers.ours(listing.get("ticker"))
            if instrument is None:
                raise BrokerError("a Trading 212 position maps to no instrument in the universe")
            if listing.get("currency") != instrument.currency.value:
                raise BrokerError(
                    f"Trading 212 prices {instrument.ticker} in another currency than configured"
                )
            found.append((instrument, position))
        return found

    def positions(self) -> Sequence[Position]:
        held = [Position(i, decimal(p, "quantity")) for i, p in self._positions()]
        return sorted((p for p in held if p.quantity > 0), key=lambda p: p.instrument.ticker)

    def quote(self, instrument: Instrument) -> Money:
        for held, position in self._positions():
            if held == instrument:
                price = decimal(position, "currentPrice")
                if price <= 0:
                    raise BrokerError(f"Trading 212 sent a non-positive price for {instrument.ticker}")
                return Money(price, instrument.currency)
        raise NoQuoteError(f"no Trading 212 price for {instrument.ticker}: not held")

    def order(self, order_id: str) -> BrokerOrder:
        if not order_id.isdigit():
            raise BrokerError("a Trading 212 order ID is a number")
        try:
            parsed = parse_order(self._client.get(f"/equity/orders/{order_id}", "order"), self._tickers)
        except NotFoundError:  # no longer working: it is in the history
            parsed = find_order(self._client, self._tickers, order_id, self._clock())
        if parsed is None or parsed.order_id != order_id:
            raise BrokerError(f"unknown order {order_id}")
        return parsed

    def recent_orders(self) -> Sequence[BrokerOrder]:
        return recent_orders(self._client, self._tickers, self._clock())

    def place(self, order: ApprovedOrder) -> str:
        if not isinstance(order, ApprovedOrder):
            raise TypeError("the broker accepts only an ApprovedOrder")
        o = order.order
        if o.quantity != o.quantity.quantize(STEP):
            raise BrokerError(f"{o.instrument.ticker}: quantity {o.quantity} is finer than {STEP}")
        quantity = float(o.quantity if o.side is Side.BUY else -o.quantity)  # exact: 3 decimals
        body = {"ticker": self._tickers.broker(o.instrument), "quantity": quantity, "extendedHours": False}
        reply = self._client.post("/equity/orders/market", body, "market")
        order_id = reply.get("id") if isinstance(reply, dict) else None
        if isinstance(order_id, bool) or not isinstance(order_id, int | str):
            raise BrokerTimeoutError("Trading 212 accepted the order but sent no order ID")
        return str(order_id)
