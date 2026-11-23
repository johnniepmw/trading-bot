"""Trading 212 orders, parsed into the broker port's types (Phase 2 step 4).

Shapes from the owner's demo API check on 2026-11-23 (`scripts/phase0_api_check.py`):
a history item is `{"order": {...}, "fill": {...}}` (no `fill` when nothing filled); sells
have negative quantities; `side` says BUY or SELL. Anything unexpected is a BrokerError, so
reconcile stops rather than guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from tradingbot.brokers.port import BrokerError, BrokerOrder, OrderStatus
from tradingbot.domain.market import Instrument, Order, Side

# Trading 212's tickers for our funds, checked in the owner's API check (2026-11-23): each
# is the London listing; currencies match bot.toml. SSAC is the benchmark, never traded.
BROKER_TICKERS: Mapping[str, str] = {
    "VWRP": "VWRPl_EQ",
    "VUKE": "VUKEl_EQ",
    "EMIM": "EMIMl_EQ",
    "IGLT": "IGLTl_EQ",
    "VAGP": "VAGPl_EQ",
    "INXG": "INXGl_EQ",
    "SGLN": "SGLNl_EQ",
}

_WORKING = frozenset({"LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "REPLACING", "REPLACED", "CANCELLING"})
_FINISHED = {
    "FILLED": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
}


class Tickers:
    """Our instruments to Trading 212's tickers and back."""

    def __init__(self, universe: frozenset[Instrument]) -> None:
        unknown = sorted(i.ticker for i in universe if i.ticker not in BROKER_TICKERS)
        if unknown:
            raise BrokerError(f"no Trading 212 ticker for {', '.join(unknown)}")
        self._to_broker = {i: BROKER_TICKERS[i.ticker] for i in universe}
        self._from_broker = {t: i for i, t in self._to_broker.items()}

    def broker(self, instrument: Instrument) -> str:
        try:
            return self._to_broker[instrument]
        except KeyError:
            raise BrokerError(f"{instrument.ticker} is not in the traded universe") from None

    def ours(self, broker_ticker: object) -> Instrument | None:
        return self._from_broker.get(broker_ticker) if isinstance(broker_ticker, str) else None


def decimal(raw: Mapping[str, object], field: str) -> Decimal:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, Decimal | int) or not Decimal(value).is_finite():
        raise BrokerError(f"Trading 212 sent an unreadable {field}")
    return Decimal(value)


def timestamp(raw: Mapping[str, object], field: str) -> datetime:
    value = raw.get(field)
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise BrokerError(f"Trading 212 sent an unreadable {field}")
    return parsed.astimezone(UTC)


def _status(name: object, filled: Decimal) -> OrderStatus:
    if name == "PARTIALLY_FILLED" or (name in _WORKING and filled > 0):
        return OrderStatus.PARTIALLY_FILLED
    if name in _WORKING:
        return OrderStatus.PENDING
    if isinstance(name, str) and name in _FINISHED:
        return _FINISHED[name]
    raise BrokerError("Trading 212 sent an order status this bot doesn't know")


def parse_order(raw: object, tickers: Tickers) -> BrokerOrder | None:
    """An order (active, or a history item's `order`). None for an instrument outside our
    universe, or a finished order that isn't a market order (the owner's own trades: the
    bot sends only market orders, so neither can be one of its intents). A working
    non-market order on one of our funds is a BrokerError: it could trade against the bot."""
    if not isinstance(raw, dict):
        raise BrokerError("Trading 212 sent an order that is not an object")
    instrument = tickers.ours(raw.get("ticker"))
    if instrument is None:
        return None
    if raw.get("type") != "MARKET":
        if raw.get("status") in _FINISHED:
            return None
        raise BrokerError(
            f"a working {instrument.ticker} order that is not a market order: cancel it in the app"
        )
    quantity, filled = (
        decimal(raw, "quantity"),
        decimal(raw, "filledQuantity") if "filledQuantity" in raw else Decimal(0),
    )
    named = raw.get("side")
    side = Side(named) if named in ("BUY", "SELL") else (Side.BUY if quantity > 0 else Side.SELL)
    if quantity == 0 or (side is Side.BUY) != (quantity > 0):
        raise BrokerError(f"a {instrument.ticker} order whose side and quantity disagree")
    order_id = raw.get("id")
    if isinstance(order_id, bool) or not isinstance(order_id, int | str):
        raise BrokerError("Trading 212 sent an order without an id")
    return BrokerOrder(
        order_id=str(order_id),
        order=Order(instrument, side, abs(quantity)),
        status=_status(raw.get("status"), abs(filled)),
        filled_quantity=abs(filled),
        created_at=timestamp(raw, "createdAt"),
    )
