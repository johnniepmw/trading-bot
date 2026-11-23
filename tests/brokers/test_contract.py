"""The broker contract (ADR-0008; Phase 2 AC11): what reconcile, the executor and the cycle
rely on, run against the fake broker and against the Trading 212 adapter talking to a
simulator that replies in the shapes recorded from the demo API (`t212_sim.py`)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest

from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import TERMINAL, Broker, BrokerError, NoQuoteError, OrderStatus
from tradingbot.brokers.trading212.broker import Trading212Broker
from tradingbot.brokers.trading212.client import Trading212Client
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money

from .t212_sim import NOW, Trading212Sim

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
CASH = Decimal(1000)
VWRP_PRICE, SGLN_PRICE = Decimal(100), Decimal(4000)  # pounds; pence


@dataclass
class Harness:
    broker: Broker
    close_market: Callable[[], None]
    open_market: Callable[[], None]
    holds_quotes_only: bool  # Trading 212 prices held positions only


def fake() -> Harness:
    b = FakeBroker(
        Money(CASH, Currency.GBP),
        {VWRP: Money(VWRP_PRICE, Currency.GBP), SGLN: Money(SGLN_PRICE, Currency.GBX)},
    )
    b.now = lambda: NOW

    def close() -> None:
        b.market_open = False

    return Harness(b, close, b.open_market, holds_quotes_only=False)


def trading212() -> Harness:
    sim = Trading212Sim(CASH, {"VWRPl_EQ": VWRP_PRICE, "SGLNl_EQ": SGLN_PRICE})
    client = Trading212Client(sim, "key:secret", clock=lambda: 0.0, sleep=lambda _: None)
    broker = Trading212Broker(client, frozenset({VWRP, SGLN}), clock=lambda: NOW)

    def close() -> None:
        sim.market_open = False

    return Harness(broker, close, sim.open_market, holds_quotes_only=True)


@pytest.fixture(params=[fake, trading212], ids=["fake", "trading212"])
def h(request: pytest.FixtureRequest) -> Iterator[Harness]:
    yield request.param()


def approved(side: Side, qty: str, inst: Instrument = VWRP) -> ApprovedOrder:
    return ApprovedOrder(Order(inst, side, Decimal(qty)))


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def test_a_new_account_has_cash_and_no_positions(h: Harness) -> None:
    assert h.broker.account().cash == gbp(1000)
    assert h.broker.account().equity == gbp(1000)
    assert h.broker.positions() == []


def test_a_market_buy_fills_and_shows_as_a_position(h: Harness) -> None:
    state = h.broker.order(h.broker.place(approved(Side.BUY, "2")))
    assert (state.status, state.filled_quantity) == (OrderStatus.FILLED, Decimal(2))
    assert state.order == Order(VWRP, Side.BUY, Decimal(2))
    assert h.broker.positions() == [Position(VWRP, Decimal(2))]
    assert h.broker.account().cash == gbp(800)
    assert h.broker.account().equity == gbp(1000)


def test_pence_quotes_are_paid_in_pounds(h: Harness) -> None:
    h.broker.place(approved(Side.BUY, "5", SGLN))  # 5 x 4000p = £200
    assert h.broker.account().cash == gbp(800)
    assert h.broker.quote(SGLN) == Money(SGLN_PRICE, Currency.GBX)


def test_a_sell_reduces_the_position_and_adds_cash(h: Harness) -> None:
    h.broker.place(approved(Side.BUY, "2"))
    sell = h.broker.order(h.broker.place(approved(Side.SELL, "0.5")))
    assert sell.order == Order(VWRP, Side.SELL, Decimal("0.5"))
    assert h.broker.positions() == [Position(VWRP, Decimal("1.5"))]
    assert h.broker.account().cash == gbp(850)


def test_fractional_quantities_survive_the_round_trip(h: Harness) -> None:
    state = h.broker.order(h.broker.place(approved(Side.BUY, "0.123")))
    assert state.order.quantity == Decimal("0.123")
    assert h.broker.positions() == [Position(VWRP, Decimal("0.123"))]


@pytest.mark.parametrize(
    "order", [approved(Side.BUY, "11"), approved(Side.SELL, "1")], ids=["over-cash", "not-held"]
)
def test_orders_the_account_cannot_cover_never_fill(h: Harness, order: ApprovedOrder) -> None:
    # the fake accepts and then rejects; Trading 212 may refuse outright: either way, nothing fills
    try:
        status = h.broker.order(h.broker.place(order)).status
    except BrokerError:
        status = OrderStatus.REJECTED
    assert status is OrderStatus.REJECTED
    assert h.broker.positions() == []
    assert h.broker.account().cash == gbp(1000)


def test_only_approved_orders_are_accepted(h: Harness) -> None:
    with pytest.raises(TypeError, match="ApprovedOrder"):
        h.broker.place(Order(VWRP, Side.BUY, Decimal(1)))  # type: ignore[arg-type]


def test_recent_orders_list_what_the_broker_received_oldest_first(h: Harness) -> None:
    first = h.broker.place(approved(Side.BUY, "1"))
    second = h.broker.place(approved(Side.BUY, "2"))
    assert [o.order_id for o in h.broker.recent_orders()] == [first, second]
    assert h.broker.recent_orders()[0].order == Order(VWRP, Side.BUY, Decimal(1))


def test_unknown_order_ids_are_an_error(h: Harness) -> None:
    with pytest.raises(BrokerError, match="unknown"):
        h.broker.order("12345")


def test_held_funds_have_a_quote_in_their_own_currency(h: Harness) -> None:
    h.broker.place(approved(Side.BUY, "1"))
    assert h.broker.quote(VWRP) == gbp(100)


def test_a_fund_not_held_has_a_quote_or_a_no_quote_error(h: Harness) -> None:
    if h.holds_quotes_only:
        with pytest.raises(NoQuoteError):
            h.broker.quote(SGLN)
    else:
        assert h.broker.quote(SGLN) == Money(SGLN_PRICE, Currency.GBX)


def test_market_orders_queue_while_the_market_is_closed(h: Harness) -> None:
    h.close_market()
    order_id = h.broker.place(approved(Side.BUY, "2"))
    state = h.broker.order(order_id)
    assert (state.status, state.filled_quantity) == (OrderStatus.PENDING, Decimal(0))
    assert state.status not in TERMINAL
    assert [o.order_id for o in h.broker.recent_orders()] == [order_id]
    assert h.broker.positions() == []
    h.open_market()
    assert h.broker.order(order_id).status is OrderStatus.FILLED
    assert h.broker.positions() == [Position(VWRP, Decimal(2))]


def test_order_ids_are_distinct(h: Harness) -> None:
    assert h.broker.place(approved(Side.BUY, "1")) != h.broker.place(approved(Side.BUY, "1"))


def test_orders_carry_their_creation_time(h: Harness) -> None:
    created = h.broker.order(h.broker.place(approved(Side.BUY, "1"))).created_at
    assert created.tzinfo is not None
    assert abs((created - NOW).total_seconds()) < 60


# --- monitoring AC4: fill price and fees ---------------------------------------------
def test_a_filled_order_reports_its_price_in_the_quote_currency_and_its_fees(h: Harness) -> None:
    state = h.broker.order(h.broker.place(approved(Side.BUY, "5", SGLN)))
    assert state.average_price == Money(SGLN_PRICE, Currency.GBX)  # pence, as recorded
    assert state.fees == gbp(0)


def test_a_working_order_has_no_fill_price(h: Harness) -> None:
    h.close_market()
    state = h.broker.order(h.broker.place(approved(Side.BUY, "1")))
    assert (state.average_price, state.fees) == (None, None)


def test_recent_orders_carry_fill_prices(h: Harness) -> None:
    h.broker.place(approved(Side.BUY, "1"))
    (order,) = h.broker.recent_orders()
    assert order.average_price == gbp(100)
