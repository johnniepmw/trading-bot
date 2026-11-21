from __future__ import annotations

import ast
import sqlite3
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
from tradingbot.execution.executor import ExecutionReport, execute, reconcile
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
    fake = FakeBroker(cash=gbp(1000), quotes={VWRP: gbp(100), SGLN: Money(Decimal(4000), Currency.GBX)})
    fake.now = lambda: NOW
    return fake


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.create(tmp_path / "ledger.sqlite")
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
    assert [i.state for i in ledger.settled()] == [IntentState.FILLED, IntentState.REJECTED]


def test_one_broker_order_is_matched_to_at_most_one_intent(broker: FakeBroker, ledger: Ledger) -> None:
    broker.place(approved(Side.BUY, "1"))  # placed once
    for _ in range(2):
        ledger.mark_unknown(ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    result = reconcile(broker, ledger)
    assert not result.clean  # the second unknown intent has no order left to match
    assert len(ledger.open_intents()) == 1


# --- review round 1 ----------------------------------------------------------------
def run_execute(orders: list[ApprovedOrder], broker: FakeBroker, ledger: Ledger) -> object:
    return execute(orders, broker, ledger, run_id="run-1", now=NOW, may_send=allowed)


def test_execute_refuses_while_any_intent_is_open(broker: FakeBroker, ledger: Ledger) -> None:
    ledger.mark_unknown(ledger.record_intent("run-0", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    report = run_execute([approved(Side.BUY, "1")], broker, ledger)
    assert report == ExecutionReport(0, "open intents: reconcile first")
    assert broker.recent_orders() == []


def test_executing_twice_in_one_run_does_not_send_twice(broker: FakeBroker, ledger: Ledger) -> None:
    orders = [approved(Side.BUY, "1")]
    run_execute(orders, broker, ledger)
    second = run_execute(orders, broker, ledger)  # the first order is still SENT (open)
    assert second.stopped == "open intents: reconcile first"
    assert len(broker.recent_orders()) == 1


class Exploding(FakeBroker):
    def place(self, order: ApprovedOrder) -> str:
        super().place(order)  # the order reaches the broker...
        raise KeyError("orderId")  # ...then the adapter fails to read the response


def test_an_unexpected_error_while_sending_marks_the_outcome_unknown(ledger: Ledger) -> None:
    broker = Exploding(cash=gbp(1000), quotes={VWRP: gbp(100)})
    broker.now = lambda: NOW
    with pytest.raises(KeyError):
        run_execute([approved(Side.BUY, "1")], broker, ledger)
    assert [i.state for i in ledger.open_intents()] == [IntentState.UNKNOWN]  # never NOT_SENT


def test_a_pending_order_keeps_its_intent_open_and_blocks_trading(broker: FakeBroker, ledger: Ledger) -> None:
    broker.market_open = False
    run_execute([approved(Side.BUY, "1")], broker, ledger)
    result = reconcile(broker, ledger)
    assert not result.clean and "PENDING" in result.problems[0]
    assert [i.state for i in ledger.open_intents()] == [IntentState.SENT]
    broker.open_market()
    assert reconcile(broker, ledger).clean


def test_a_partly_filled_order_is_not_settled_until_it_finishes(broker: FakeBroker, ledger: Ledger) -> None:
    broker.partial_fill_next = Decimal("0.5")
    run_execute([approved(Side.BUY, "2")], broker, ledger)
    assert not reconcile(broker, ledger).clean  # the rest may still fill
    broker.open_market()
    assert reconcile(broker, ledger).clean
    assert ledger.settled()[-1].filled_quantity == Decimal(2)


def test_a_cancelled_order_records_what_did_fill(broker: FakeBroker, ledger: Ledger) -> None:
    broker.partial_fill_next = Decimal("0.5")
    order_id = run_execute([approved(Side.BUY, "2")], broker, ledger) and broker.recent_orders()[0].order_id
    broker.cancel(order_id)
    assert reconcile(broker, ledger).clean
    settled = ledger.settled()[-1]
    assert (settled.state, settled.filled_quantity) == (IntentState.CANCELLED, Decimal(1))


def test_an_unknown_intent_only_matches_broker_orders_created_after_it(
    broker: FakeBroker, ledger: Ledger
) -> None:
    broker.now = lambda: datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    broker.place(approved(Side.BUY, "1"))  # an older, identical (e.g. manual) order
    ledger.mark_unknown(ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    result = reconcile(broker, ledger)
    assert not result.clean  # the old order must not be taken as ours


def test_two_candidate_broker_orders_are_a_problem(broker: FakeBroker, ledger: Ledger) -> None:
    broker.now = lambda: NOW
    broker.place(approved(Side.BUY, "1"))
    broker.place(approved(Side.BUY, "1"))
    ledger.mark_unknown(ledger.record_intent("run-1", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    result = reconcile(broker, ledger)
    assert not result.clean and "2 broker orders" in result.problems[0]


def test_an_unfinished_broker_order_no_intent_knows_about_is_a_problem(
    broker: FakeBroker, ledger: Ledger
) -> None:
    broker.market_open = False
    broker.place(approved(Side.BUY, "1"))  # e.g. placed by hand, or by a run whose ledger was lost
    result = reconcile(broker, ledger)
    assert not result.clean and "no intent" in result.problems[0]


def test_a_failure_recording_the_send_marks_the_outcome_unknown(broker: FakeBroker, ledger: Ledger) -> None:
    def broken(intent_id: int, broker_order_id: str) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    ledger.mark_sent = broken  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        run_execute([approved(Side.BUY, "1")], broker, ledger)
    assert [i.state for i in ledger.open_intents()] == [IntentState.UNKNOWN]


def test_only_the_executor_calls_broker_place() -> None:
    # app gets ApprovedOrders from risk.review; calling place() directly would skip the
    # kill switch and the ledger
    src = Path(__file__).resolve().parents[2] / "src" / "tradingbot"
    callers = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        for node in ast.walk(ast.parse(p.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "place"
    ]
    assert callers == ["execution/executor.py"]
