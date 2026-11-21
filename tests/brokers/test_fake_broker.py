"""The fake broker's behaviour, written as a contract the Trading 212 adapter must also pass
(recorded responses, after the owner's API check). The fake must never be more capable
than the real API: market orders only, no client order IDs, broker-assigned order IDs."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import BrokerError, BrokerTimeoutError, OrderStatus
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)


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
