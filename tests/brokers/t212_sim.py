"""An in-memory stand-in for Trading 212's demo API, replying in the shapes recorded from the
real one on 2026-11-23 (scripts/t212_record.py). Values are made up (ADR-0014).

What is known from the recordings is modelled as recorded; what isn't (how an order the
account can't cover is refused) is a guess, and the contract tests accept either outcome.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingbot.brokers.trading212.client import DEMO_BASE

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
CURRENCY = {"VWRPl_EQ": "GBP", "SGLNl_EQ": "GBX"}


def _problem(status: int, code: str) -> tuple[int, bytes]:
    body = {"type": f"/api-errors/{code}", "title": "t", "status": status, "detail": "d", "traceId": "x"}
    return status, json.dumps(body).encode()


def _num(value: Decimal) -> float:
    return float(value)


class Trading212Sim:
    def __init__(self, cash: Decimal, prices: Mapping[str, Decimal]) -> None:
        self.cash = cash
        self.prices = dict(prices)  # in each instrument's currency (pence for GBX)
        self.held: dict[str, Decimal] = {}
        self.working: dict[int, dict[str, object]] = {}
        self.history: list[dict[str, object]] = []  # newest last
        self.market_open = True
        self.ids = itertools.count(58500000000)
        self.ticks = itertools.count()
        self.calls: list[tuple[str, str]] = []

    # --- the transport ------------------------------------------------------------------
    def __call__(  # noqa: PLR0911 - one return per mocked route
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        assert url.startswith(DEMO_BASE)
        assert headers["Authorization"].startswith("Basic ")
        path = url.removeprefix(DEMO_BASE)
        self.calls.append((method, path))
        if method == "POST" and path == "/equity/orders/market":
            return self._market(json.loads(body or b"{}", parse_float=Decimal))
        route = path.split("?")[0]
        if route == "/equity/account/summary":
            return self._ok(self._summary())
        if route == "/equity/positions":
            return self._ok([self._position(t, q) for t, q in self.held.items() if q])
        if route == "/equity/orders":
            return self._ok(list(self.working.values()))
        if route.startswith("/equity/orders/"):
            order = self.working.get(int(route.rsplit("/", 1)[1]))
            return self._ok(order) if order else _problem(404, "not-found")
        if route == "/equity/history/orders":
            return self._ok({"items": list(reversed(self.history)), "nextPagePath": None})
        return _problem(404, "no-route")

    @staticmethod
    def _ok(data: object) -> tuple[int, bytes]:
        return 200, json.dumps(data, default=_num).encode()

    def _stamp(self) -> str:
        return (
            (NOW + timedelta(seconds=next(self.ticks)))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _value(self, ticker: str, quantity: Decimal) -> Decimal:
        price = self.prices[ticker]
        return quantity * (price / 100 if CURRENCY[ticker] == "GBX" else price)

    def _summary(self) -> dict[str, object]:
        invested = sum((self._value(t, q) for t, q in self.held.items()), Decimal(0))
        return {
            "id": 1,
            "currency": "GBP",
            "totalValue": self.cash + invested,
            "cash": {"availableToTrade": self.cash, "reservedForOrders": 0, "inPies": 0},
            "investments": {
                "currentValue": invested,
                "totalCost": invested,
                "realizedProfitLoss": 0,
                "unrealizedProfitLoss": 0,
            },
        }

    def _instrument(self, ticker: str) -> dict[str, object]:
        return {"ticker": ticker, "name": "Fund", "isin": "IE0000000000", "currency": CURRENCY[ticker]}

    def _position(self, ticker: str, quantity: Decimal) -> dict[str, object]:
        value = self._value(ticker, quantity)
        return {
            "instrument": self._instrument(ticker),
            "createdAt": "2026-11-23T09:00:00.000+00:00",
            "quantity": quantity,
            "quantityAvailableForTrading": quantity,
            "quantityInPies": 0,
            "currentPrice": self.prices[ticker],
            "averagePricePaid": self.prices[ticker],
            "walletImpact": {
                "currency": "GBP",
                "totalCost": value,
                "currentValue": value,
                "unrealizedProfitLoss": 0,
                "fxImpact": None,
            },
        }

    # --- orders -------------------------------------------------------------------------
    def _market(self, body: Mapping[str, object]) -> tuple[int, bytes]:
        ticker, quantity = body.get("ticker"), body.get("quantity")
        if ticker not in self.prices:
            return _problem(404, "instrument-not-found")
        if not isinstance(quantity, Decimal | int) or quantity == 0:
            return _problem(400, "bad-request")
        quantity = Decimal(quantity)
        if quantity != quantity.quantize(Decimal("0.001")):
            return _problem(400, "quantity-precision-mismatch")
        if quantity > 0 and self._value(ticker, quantity) > self.cash:
            return _problem(400, "insufficient-funds")  # a guess: unrecorded
        if quantity < 0 and -quantity > self.held.get(ticker, Decimal(0)):
            return _problem(400, "selling-equity-not-owned")  # a guess: unrecorded
        order: dict[str, object] = {
            "id": next(self.ids),
            "strategy": "QUANTITY",
            "type": "MARKET",
            "ticker": ticker,
            "quantity": quantity,
            "filledQuantity": 0,
            "status": "NEW",
            "currency": "GBP",
            "extendedHours": False,
            "initiatedFrom": "API",
            "side": "BUY" if quantity > 0 else "SELL",
            "createdAt": self._stamp(),
            "instrument": self._instrument(ticker),
        }
        self.working[int(order["id"])] = order  # type: ignore[call-overload]
        reply = dict(order)
        if self.market_open:
            self._fill(int(order["id"]))  # type: ignore[call-overload]
        return self._ok(reply)

    def _fill(self, order_id: int) -> None:
        order = self.working.pop(order_id)
        ticker, quantity = str(order["ticker"]), Decimal(str(order["quantity"]))
        value = self._value(ticker, quantity)
        self.cash -= value
        self.held[ticker] = self.held.get(ticker, Decimal(0)) + quantity
        order.update(status="FILLED", filledQuantity=quantity)
        fill = {
            "id": next(self.ids),
            "quantity": quantity,
            "price": self.prices[ticker],
            "type": "TRADE",
            "tradingMethod": "OTC",
            "filledAt": self._stamp(),
            "walletImpact": {"currency": "GBP", "netValue": abs(value), "fxRate": 1, "taxes": []},
        }
        self.history.append({"order": order, "fill": fill})

    def open_market(self) -> None:
        self.market_open = True
        for order_id in list(self.working):
            self._fill(order_id)
