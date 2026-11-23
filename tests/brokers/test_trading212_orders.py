"""Parsing Trading 212 orders. The JSON follows the shape recorded from the demo API on
2026-11-23, with made-up values (the repository is public: ADR-0014)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingbot.brokers.port import BrokerError, OrderStatus
from tradingbot.brokers.trading212.orders import BROKER_TICKERS, Tickers, parse_order
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
TICKERS = Tickers(frozenset({VWRP, SGLN}))

SELL_ITEM = json.loads(
    """{
 "order": {"id": 111, "strategy": "QUANTITY", "type": "MARKET", "ticker": "VWRPl_EQ",
  "quantity": -0.25, "filledQuantity": -0.25, "status": "FILLED", "currency": "GBP",
  "extendedHours": false, "initiatedFrom": "API", "side": "SELL",
  "createdAt": "2026-11-23T08:24:16.000Z",
  "instrument": {"ticker": "VWRPl_EQ", "name": "Fund", "isin": "IE0000000000", "currency": "GBP"}},
 "fill": {"id": 112, "quantity": -0.25, "price": 100.5, "type": "TRADE", "tradingMethod": "OTC",
  "filledAt": "2026-11-23T08:24:17.000Z",
  "walletImpact": {"currency": "GBP", "netValue": 25.12, "realisedProfitLoss": 0.1, "fxRate": 1, "taxes": []}}
}""",
    parse_float=Decimal,
)


def order(**changes: object) -> dict[str, object]:
    raw = dict(SELL_ITEM["order"])
    raw.update(changes)
    return raw


def test_a_filled_sell_from_the_history() -> None:
    parsed = parse_order(SELL_ITEM["order"], TICKERS)
    assert parsed is not None
    assert parsed.order_id == "111"
    assert parsed.order == Order(VWRP, Side.SELL, Decimal("0.25"))
    assert (parsed.status, parsed.filled_quantity) == (OrderStatus.FILLED, Decimal("0.25"))
    assert parsed.created_at == datetime(2026, 11, 23, 8, 24, 16, tzinfo=UTC)


def test_a_buy_has_a_positive_quantity() -> None:
    parsed = parse_order(order(side="BUY", quantity=Decimal("0.5"), filledQuantity=0, status="NEW"), TICKERS)
    assert parsed is not None
    assert parsed.order == Order(VWRP, Side.BUY, Decimal("0.5"))
    assert (parsed.status, parsed.filled_quantity) == (OrderStatus.PENDING, Decimal(0))


@pytest.mark.parametrize(
    "status", ["LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "REPLACING", "REPLACED", "CANCELLING"]
)
def test_working_statuses_are_pending_until_something_fills(status: str) -> None:
    parsed = parse_order(order(status=status, filledQuantity=0), TICKERS)
    assert parsed is not None and parsed.status is OrderStatus.PENDING


def test_a_working_order_with_a_fill_is_partly_filled() -> None:
    parsed = parse_order(order(status="NEW", filledQuantity=Decimal("-0.1")), TICKERS)
    assert parsed is not None
    assert (parsed.status, parsed.filled_quantity) == (OrderStatus.PARTIALLY_FILLED, Decimal("0.1"))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PARTIALLY_FILLED", OrderStatus.PARTIALLY_FILLED),
        ("REJECTED", OrderStatus.REJECTED),
        ("CANCELLED", OrderStatus.CANCELLED),
    ],
)
def test_finished_and_partial_statuses(status: str, expected: OrderStatus) -> None:
    parsed = parse_order(order(status=status), TICKERS)
    assert parsed is not None and parsed.status is expected


def test_an_unknown_status_stops_rather_than_guesses() -> None:
    with pytest.raises(BrokerError, match="status"):
        parse_order(order(status="SOMETHING_NEW"), TICKERS)


def test_an_order_without_a_filled_quantity_has_filled_nothing() -> None:
    raw = order(status="NEW")
    del raw["filledQuantity"]
    parsed = parse_order(raw, TICKERS)
    assert parsed is not None and parsed.filled_quantity == 0


def test_a_fund_outside_the_universe_is_ignored() -> None:
    assert parse_order(order(ticker="AAPL_US_EQ"), TICKERS) is None


def test_a_finished_limit_order_is_ignored() -> None:
    assert parse_order(order(type="LIMIT", status="CANCELLED"), TICKERS) is None


def test_a_replaced_limit_order_is_ignored() -> None:
    assert parse_order(order(type="LIMIT", status="REPLACED"), TICKERS) is None


def test_a_working_limit_order_on_our_fund_stops_the_run() -> None:
    with pytest.raises(BrokerError, match="cancel it in the app"):
        parse_order(order(type="LIMIT", status="NEW"), TICKERS)


def test_side_and_quantity_must_agree() -> None:
    with pytest.raises(BrokerError, match="disagree"):
        parse_order(order(side="BUY"), TICKERS)


def test_without_a_side_the_sign_decides() -> None:
    raw = order()
    del raw["side"]
    parsed = parse_order(raw, TICKERS)
    assert parsed is not None and parsed.order.side is Side.SELL


@pytest.mark.parametrize("quantity", [0, True, "1", None, Decimal("NaN")])
def test_unreadable_quantities_are_refused(quantity: object) -> None:
    with pytest.raises(BrokerError):
        parse_order(order(quantity=quantity), TICKERS)


@pytest.mark.parametrize("order_id", [None, True, 1.5])
def test_an_order_needs_an_id(order_id: object) -> None:
    with pytest.raises(BrokerError, match="id"):
        parse_order(order(id=order_id), TICKERS)


@pytest.mark.parametrize("created", [None, "yesterday", "2026-11-23T08:24:16"])
def test_creation_time_must_be_a_full_timestamp(created: object) -> None:
    with pytest.raises(BrokerError, match="createdAt"):
        parse_order(order(createdAt=created), TICKERS)


def test_an_order_must_be_an_object() -> None:
    with pytest.raises(BrokerError, match="object"):
        parse_order([1], TICKERS)


def test_every_traded_fund_has_a_broker_ticker() -> None:
    # bot.toml's universe; SSAC is the benchmark and never traded
    assert set(BROKER_TICKERS) == {"VWRP", "VUKE", "EMIM", "IGLT", "VAGP", "INXG", "SGLN"}


def test_a_fund_without_a_broker_ticker_is_refused_at_start() -> None:
    with pytest.raises(BrokerError, match="SSAC"):
        Tickers(frozenset({Instrument("SSAC", Currency.GBX)}))


def test_tickers_map_both_ways() -> None:
    assert TICKERS.broker(SGLN) == "SGLNl_EQ"
    assert TICKERS.ours("SGLNl_EQ") == SGLN
    assert TICKERS.ours(5) is None
    with pytest.raises(BrokerError, match="universe"):
        TICKERS.broker(Instrument("VUKE", Currency.GBP))
