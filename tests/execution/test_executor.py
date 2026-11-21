from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import BrokerError
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.execution.executor import execute, reconcile
from tradingbot.ledger.store import IntentState, Ledger

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def approved(side: Side, qty: str, inst: Instrument = VWRP) -> ApprovedOrder:
    return ApprovedOrder(Order(inst, side, Decimal(qty)))


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker(cash=gbp(1000), quotes={VWRP: gbp(100), SGLN: Money(Decimal(4000), Currency.GBX)})


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.open(tmp_path / "ledger.sqlite")
    yield opened
    opened.close()


def allowed() -> bool:
    return True


def test_each_order_is_recorded_then_sent(broker: FakeBroker, ledger: Ledger) -> None:
    report = execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "1", SGLN)],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
    )
    assert report.sent == 2 and not report.stopped
    assert [i.state for i in ledger.open_intents()] == [IntentState.SENT, IntentState.SENT]
    assert len(broker.recent_orders()) == 2


def test_only_approved_orders_can_be_executed(broker: FakeBroker, ledger: Ledger) -> None:
    with pytest.raises(TypeError, match="ApprovedOrder"):
        execute(
            [Order(VWRP, Side.BUY, Decimal(1))], broker, ledger, run_id="run-1", now=NOW, may_send=allowed
        )  # type: ignore[list-item]
    assert ledger.open_intents() == [] and broker.recent_orders() == []


def test_the_kill_switch_is_checked_before_every_order(broker: FakeBroker, ledger: Ledger) -> None:
    answers = iter([True, False])
    report = execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "1")],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=lambda: next(answers),
    )
    assert (report.sent, report.stopped) == (1, "kill switch off")
    assert len(broker.recent_orders()) == 1
    assert len(ledger.open_intents()) == 1  # nothing recorded for the order not attempted


def test_a_timeout_marks_the_outcome_unknown_and_stops(broker: FakeBroker, ledger: Ledger) -> None:
    broker.timeout_after_accepting_next = True
    report = execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "1", SGLN)],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
    )
    assert (report.sent, report.stopped) == (0, "broker timeout: outcome unknown")
    assert [i.state for i in ledger.open_intents()] == [IntentState.UNKNOWN]
    assert len(broker.recent_orders()) == 1  # the second order was never tried


def test_a_broker_error_settles_that_intent_and_stops(broker: FakeBroker, ledger: Ledger) -> None:
    broker.fail_next = BrokerError("503")
    report = execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "1", SGLN)],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
    )
    assert report.stopped == "broker error: 503"
    assert ledger.open_intents() == []
    assert broker.recent_orders() == []


# --- reconcile: every run starts here ----------------------------------------------
def test_reconcile_finds_an_order_that_timed_out_but_was_placed(broker: FakeBroker, ledger: Ledger) -> None:
    broker.timeout_after_accepting_next = True
    execute([approved(Side.BUY, "1")], broker, ledger, run_id="run-1", now=NOW, may_send=allowed)
    result = reconcile(broker, ledger)
    assert result.clean
    assert ledger.open_intents() == []  # matched to the broker's order and settled as filled
    assert len(broker.recent_orders()) == 1  # and never sent again


def test_reconcile_settles_an_intent_the_broker_never_received(broker: FakeBroker, ledger: Ledger) -> None:
    # a crash between the ledger write and the send
    ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW)
    result = reconcile(broker, ledger)
    assert result.clean
    assert ledger.open_intents() == []
    assert broker.recent_orders() == []  # not resent: the next run decides afresh


def test_an_unknown_outcome_with_no_matching_broker_order_blocks_trading(
    broker: FakeBroker, ledger: Ledger
) -> None:
    intent = ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW)
    ledger.mark_unknown(intent)
    result = reconcile(broker, ledger)
    assert not result.clean
    assert "unknown" in result.problems[0]


def test_sent_orders_are_settled_from_the_brokers_state(broker: FakeBroker, ledger: Ledger) -> None:
    execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "50")],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
    )  # 2nd rejected
    assert reconcile(broker, ledger).clean
    assert ledger.open_intents() == []
    assert [s for s in ledger.settled_states()] == [IntentState.FILLED, IntentState.REJECTED]


def test_one_broker_order_is_matched_to_at_most_one_intent(broker: FakeBroker, ledger: Ledger) -> None:
    broker.place(approved(Side.BUY, "1"))  # placed once
    for _ in range(2):
        ledger.mark_unknown(ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    result = reconcile(broker, ledger)
    assert not result.clean  # the second unknown intent has no order left to match
    assert len(ledger.open_intents()) == 1
