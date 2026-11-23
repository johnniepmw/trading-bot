"""The fake broker's behaviour, written as a contract the Trading 212 adapter must also pass
(recorded responses, after the owner's API check). The fake must never be more capable
than the real API: market orders only, no client order IDs, broker-assigned order IDs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import BrokerError, BrokerTimeoutError, OrderStatus
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def broker() -> FakeBroker:
    return FakeBroker(cash=gbp(1000), quotes={VWRP: gbp(100), SGLN: Money(Decimal(4000), Currency.GBX)})


def approved(side: Side, qty: str, inst: Instrument = VWRP) -> ApprovedOrder:
    return ApprovedOrder(Order(inst, side, Decimal(qty)))


def test_a_new_account_has_cash_and_no_positions() -> None:
    b = broker()
    assert b.account().cash == gbp(1000)
    assert b.account().equity == gbp(1000)
    assert b.positions() == []


def test_a_market_buy_fills_at_the_quote_and_shows_as_a_position() -> None:
    b = broker()
    order_id = b.place(approved(Side.BUY, "2"))
    state = b.order(order_id)
    assert (state.status, state.filled_quantity) == (OrderStatus.FILLED, Decimal(2))
    assert b.positions() == [Position(VWRP, Decimal(2))]
    assert b.account().cash == gbp(800)
    assert b.account().equity == gbp(1000)


def test_pence_quotes_are_paid_in_pounds() -> None:
    b = broker()
    b.place(approved(Side.BUY, "5", SGLN))  # 5 x 4000p = £200
    assert b.account().cash == gbp(800)


def test_a_sell_reduces_the_position_and_adds_cash() -> None:
    b = broker()
    b.place(approved(Side.BUY, "2"))
    b.place(approved(Side.SELL, "0.5"))
    assert b.positions() == [Position(VWRP, Decimal("1.5"))]
    assert b.account().cash == gbp(850)


def test_orders_the_account_cannot_cover_are_rejected_not_filled() -> None:
    b = broker()
    over = b.order(b.place(approved(Side.BUY, "11")))  # £1100 > £1000
    short = b.order(b.place(approved(Side.SELL, "1")))  # nothing held
    assert (over.status, short.status) == (OrderStatus.REJECTED, OrderStatus.REJECTED)
    assert b.positions() == []
    assert b.account().cash == gbp(1000)


def test_only_approved_orders_are_accepted() -> None:
    with pytest.raises(TypeError, match="ApprovedOrder"):
        broker().place(Order(VWRP, Side.BUY, Decimal(1)))  # type: ignore[arg-type]


def test_recent_orders_list_what_the_broker_received() -> None:
    b = broker()
    first = b.place(approved(Side.BUY, "1"))
    second = b.place(approved(Side.BUY, "11"))
    assert [o.order_id for o in b.recent_orders()] == [first, second]
    assert b.recent_orders()[0].order == Order(VWRP, Side.BUY, Decimal(1))


def test_a_timeout_can_happen_after_the_broker_accepted_the_order() -> None:
    # the dangerous case: we don't know it was placed, but it was
    b = broker()
    b.timeout_after_accepting_next = True
    with pytest.raises(BrokerTimeoutError):
        b.place(approved(Side.BUY, "1"))
    assert len(b.recent_orders()) == 1
    assert b.positions() == [Position(VWRP, Decimal(1))]


def test_an_error_before_acceptance_places_nothing() -> None:
    b = broker()
    b.fail_next = BrokerError("503")
    with pytest.raises(BrokerError):
        b.place(approved(Side.BUY, "1"))
    assert b.recent_orders() == []


def test_unknown_order_ids_are_an_error() -> None:
    with pytest.raises(BrokerError, match="unknown"):
        broker().order("nope")


def test_quotes_come_from_the_broker() -> None:
    assert broker().quote(SGLN) == Money(Decimal(4000), Currency.GBX)
    with pytest.raises(BrokerError, match="no quote"):
        broker().quote(Instrument("XXX", Currency.GBP))


# --- review round 1: the fake can be as awkward as the real broker -----------------
def test_market_orders_queue_while_the_market_is_closed() -> None:
    b = broker()
    b.market_open = False
    state = b.order(b.place(approved(Side.BUY, "2")))
    assert (state.status, state.filled_quantity) == (OrderStatus.PENDING, Decimal(0))
    assert b.positions() == []
    b.open_market()
    assert b.order(state.order_id).status is OrderStatus.FILLED
    assert b.positions() == [Position(VWRP, Decimal(2))]


def test_an_order_can_fill_partly_and_finish_later() -> None:
    b = broker()
    b.partial_fill_next = Decimal("0.5")
    order_id = b.place(approved(Side.BUY, "2"))
    assert (b.order(order_id).status, b.order(order_id).filled_quantity) == (
        OrderStatus.PARTIALLY_FILLED,
        Decimal(1),
    )
    b.open_market()
    assert (b.order(order_id).status, b.order(order_id).filled_quantity) == (OrderStatus.FILLED, Decimal(2))


def test_order_ids_never_repeat_across_instances() -> None:
    assert broker().place(approved(Side.BUY, "1")) != broker().place(approved(Side.BUY, "1"))


def test_broker_orders_carry_their_creation_time() -> None:
    b = broker()
    b.now = lambda: NOW
    assert b.order(b.place(approved(Side.BUY, "1"))).created_at == NOW


# --- monitoring AC4: fill price and fees ---------------------------------------------
def test_a_fill_reports_its_average_price_and_zero_fees() -> None:
    b = broker()
    state = b.order(b.place(approved(Side.BUY, "5", SGLN)))
    assert state.average_price == Money(Decimal(4000), Currency.GBX)
    assert state.fees == gbp(0)


def test_an_order_filled_in_two_parts_averages_its_prices() -> None:
    b = broker()
    b.partial_fill_next = Decimal("0.5")
    order_id = b.place(approved(Side.BUY, "2"))  # 1 at £100
    b.quotes[VWRP] = gbp(110)
    b.open_market()  # 1 at £110
    assert b.order(order_id).average_price == gbp(105)


def test_an_unfilled_order_has_no_price() -> None:
    b = broker()
    b.market_open = False
    state = b.order(b.place(approved(Side.BUY, "1")))
    assert (state.average_price, state.fees) == (None, None)


def test_fill_prices_survive_saving_the_account() -> None:
    b = broker()
    order_id = b.place(approved(Side.BUY, "1"))
    loaded = FakeBroker.load(b.dump(), b.quotes)
    assert loaded.order(order_id).average_price == gbp(100)
    assert loaded.order(order_id).fees == gbp(0)


def test_an_account_saved_before_fill_prices_still_loads() -> None:
    b = broker()
    order_id = b.place(approved(Side.BUY, "1"))
    state = b.dump()
    del state["orders"][0]["average_price"]  # type: ignore[index]
    loaded = FakeBroker.load(state, b.quotes).order(order_id)
    assert (loaded.average_price, loaded.fees) == (None, None)


def test_a_fill_of_nothing_leaves_the_order_waiting() -> None:
    b = broker()
    b.partial_fill_next = Decimal(0)
    state = b.order(b.place(approved(Side.BUY, "1")))
    assert (state.status, state.average_price) == (OrderStatus.PENDING, None)
