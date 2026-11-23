"""The Trading 212 adapter's refusals: anything unexpected is a BrokerError, never a guess."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.brokers.port import BrokerError, BrokerTimeoutError, NoQuoteError, OrderStatus
from tradingbot.brokers.trading212.broker import Trading212Broker
from tradingbot.brokers.trading212.client import Trading212Client
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency
from tradingbot.execution.executor import reconcile
from tradingbot.ledger.store import IntentState, Ledger

from .t212_sim import NOW, Trading212Sim

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)


class Canned:
    """Replies with fixed JSON per path; records what was sent."""

    def __init__(self, replies: Mapping[str, object]) -> None:
        self.replies = replies
        self.sent: list[dict[str, object]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        if body:
            self.sent.append(json.loads(body))
        path = url.split("/api/v0", 1)[1].split("?", maxsplit=1)[0]
        reply = self.replies[path]
        return reply if isinstance(reply, tuple) else (200, json.dumps(reply).encode())


def broker(transport: object) -> Trading212Broker:
    client = Trading212Client(transport, "k:s", clock=lambda: 0.0, sleep=lambda _: None)  # type: ignore[arg-type]
    return Trading212Broker(client, frozenset({VWRP, SGLN}), clock=lambda: NOW)


def position(ticker: str = "VWRPl_EQ", currency: str = "GBP", **changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "instrument": {"ticker": ticker, "currency": currency},
        "quantity": 1.5,
        "currentPrice": 100.25,
    }
    raw.update(changes)
    return raw


def test_an_account_not_in_pounds_is_refused() -> None:
    with pytest.raises(BrokerError, match="GBP"):
        broker(
            Canned(
                {
                    "/equity/account/summary": {
                        "currency": "EUR",
                        "totalValue": 1,
                        "cash": {"availableToTrade": 1},
                    }
                }
            )
        ).account()


def test_a_summary_without_cash_is_refused() -> None:
    with pytest.raises(BrokerError, match="cash"):
        broker(Canned({"/equity/account/summary": {"currency": "GBP", "totalValue": 1}})).account()


def test_a_position_outside_the_universe_is_an_error() -> None:
    with pytest.raises(BrokerError, match="maps to no instrument"):
        broker(Canned({"/equity/positions": [position("AAPL_US_EQ")]})).positions()


def test_a_position_priced_in_another_currency_is_an_error() -> None:
    with pytest.raises(BrokerError, match="another currency"):
        broker(Canned({"/equity/positions": [position("SGLNl_EQ", "GBP")]})).positions()


@pytest.mark.parametrize(
    "reply", [{"x": 1}, [1], [{"quantity": 1}]], ids=["not-a-list", "not-objects", "no-instrument"]
)
def test_unreadable_positions_are_an_error(reply: object) -> None:
    with pytest.raises(BrokerError):
        broker(Canned({"/equity/positions": reply})).positions()


def test_zero_quantity_positions_are_left_out() -> None:
    assert broker(Canned({"/equity/positions": [position(quantity=0)]})).positions() == []


def test_the_quote_is_the_held_positions_current_price() -> None:
    quote = broker(Canned({"/equity/positions": [position()]})).quote(VWRP)
    assert (quote.amount, quote.currency) == (Decimal("100.25"), Currency.GBP)


def test_a_fund_not_held_has_no_quote() -> None:
    with pytest.raises(NoQuoteError, match="SGLN"):
        broker(Canned({"/equity/positions": [position()]})).quote(SGLN)


def test_a_non_positive_price_is_refused() -> None:
    with pytest.raises(BrokerError, match="non-positive"):
        broker(Canned({"/equity/positions": [position(currentPrice=0)]})).quote(VWRP)


@pytest.mark.parametrize("order_id", ["../account", "١٢", "", "1" * 21])
def test_an_order_id_must_be_a_number(order_id: str) -> None:
    with pytest.raises(BrokerError, match="number"):
        broker(Canned({})).order(order_id)


def test_a_finished_order_is_found_in_the_history() -> None:
    sim = Trading212Sim(Decimal(1000), {"VWRPl_EQ": Decimal(100)})
    b = broker(sim)
    order_id = b.place(ApprovedOrder(Order(VWRP, Side.BUY, Decimal(1))))
    assert b.order(order_id).status is OrderStatus.FILLED
    assert ("GET", "/equity/history/orders?limit=50") in sim.calls


def test_an_order_reply_for_another_id_is_refused() -> None:
    other = {
        "id": 2,
        "type": "MARKET",
        "ticker": "VWRPl_EQ",
        "quantity": 1,
        "status": "NEW",
        "side": "BUY",
        "createdAt": "2026-11-23T09:00:00Z",
    }
    with pytest.raises(BrokerError, match="unknown order 1"):
        broker(Canned({"/equity/orders/1": other})).order("1")


def test_a_sell_is_sent_as_a_negative_quantity() -> None:
    canned = Canned({"/equity/orders/market": {"id": 7}})
    assert broker(canned).place(ApprovedOrder(Order(SGLN, Side.SELL, Decimal("0.123")))) == "7"
    assert canned.sent == [{"ticker": "SGLNl_EQ", "quantity": -0.123, "extendedHours": False}]


def test_a_quantity_finer_than_the_step_is_never_sent() -> None:
    canned = Canned({})
    with pytest.raises(BrokerError, match="finer"):
        broker(canned).place(ApprovedOrder(Order(VWRP, Side.BUY, Decimal("0.1234"))))
    assert canned.sent == []


@pytest.mark.parametrize("reply", [{}, {"id": True}, [1], {"id": "abc"}, {"id": "١٢"}])
def test_an_accepted_order_without_an_id_is_an_unknown_outcome(reply: object) -> None:
    with pytest.raises(BrokerTimeoutError, match="no usable order ID"):
        broker(Canned({"/equity/orders/market": reply})).place(
            ApprovedOrder(Order(VWRP, Side.BUY, Decimal(1)))
        )


def test_a_refused_order_is_a_broker_error_with_its_code() -> None:
    body = json.dumps({"type": "/api-errors/insufficient-funds", "detail": "987.65"}).encode()
    with pytest.raises(BrokerError, match=r"\(insufficient-funds\)$") as error:
        broker(Canned({"/equity/orders/market": (400, body)})).place(
            ApprovedOrder(Order(VWRP, Side.BUY, Decimal(1)))
        )
    assert type(error.value) is BrokerError


# --- reconcile over the adapter ------------------------------------------------------
@pytest.mark.parametrize("days_later", [1, 36])
def test_a_crash_after_sending_is_found_however_late_the_next_run(tmp_path: Path, days_later: int) -> None:
    # sent, then the job died before saving the order ID: the intent is still INTENDED
    sim = Trading212Sim(Decimal(1000), {"VWRPl_EQ": Decimal(100)})
    order = Order(VWRP, Side.BUY, Decimal(1))
    ledger = Ledger.create(tmp_path / "ledger.sqlite")
    intent = ledger.record_intent("run-1", order, NOW - timedelta(seconds=1))
    broker(sim).place(ApprovedOrder(order))
    later = Trading212Broker(
        Trading212Client(sim, "k:s", clock=lambda: 0.0, sleep=lambda _: None),
        frozenset({VWRP, SGLN}),
        clock=lambda: NOW + timedelta(days=days_later),
    )
    assert reconcile(later, ledger).clean
    (settled,) = ledger.settled()
    assert (settled.intent_id, settled.state) == (intent, IntentState.FILLED)
    ledger.close()
