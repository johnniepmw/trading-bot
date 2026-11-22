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


def nothing() -> None:
    """A checkpoint that saves nowhere (the app uploads the ledger here)."""


def test_each_order_is_recorded_then_sent(broker: FakeBroker, ledger: Ledger) -> None:
    report = execute(
        [approved(Side.BUY, "1"), approved(Side.BUY, "1", SGLN)],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
        checkpoint=nothing,
    )
    assert report.sent == 2 and not report.stopped
    assert [i.state for i in ledger.open_intents()] == [IntentState.SENT, IntentState.SENT]
    assert len(broker.recent_orders()) == 2


def test_only_approved_orders_can_be_executed(broker: FakeBroker, ledger: Ledger) -> None:
    with pytest.raises(TypeError, match="ApprovedOrder"):
        execute(
            [Order(VWRP, Side.BUY, Decimal(1))],
            broker,
            ledger,
            run_id="run-1",
            now=NOW,
            may_send=allowed,
            checkpoint=nothing,
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
        checkpoint=nothing,
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
        checkpoint=nothing,
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
        checkpoint=nothing,
    )
    assert report.stopped == "broker error: 503"
    assert ledger.open_intents() == []
    assert broker.recent_orders() == []


# --- reconcile: every run starts here ----------------------------------------------
def test_reconcile_finds_an_order_that_timed_out_but_was_placed(broker: FakeBroker, ledger: Ledger) -> None:
    broker.timeout_after_accepting_next = True
    execute(
        [approved(Side.BUY, "1")],
        broker,
        ledger,
        run_id="run-1",
        now=NOW,
        may_send=allowed,
        checkpoint=nothing,
    )
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
        checkpoint=nothing,
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
    return execute(orders, broker, ledger, run_id="run-1", now=NOW, may_send=allowed, checkpoint=nothing)


def test_execute_refuses_while_any_intent_is_open(broker: FakeBroker, ledger: Ledger) -> None:
    ledger.mark_unknown(ledger.record_intent("run-0", Order(VWRP, Side.BUY, Decimal(1)), NOW))
    report = run_execute([approved(Side.BUY, "1")], broker, ledger)
    assert report.sent == 0 and report.stopped is not None and report.stopped.startswith("not reconciled")
    assert broker.recent_orders() == []


def test_executing_twice_in_one_run_does_not_send_twice(broker: FakeBroker, ledger: Ledger) -> None:
    orders = [approved(Side.BUY, "1")]
    run_execute(orders, broker, ledger)
    second = run_execute(orders, broker, ledger)  # same run: refused even though reconcile is clean
    assert second == ExecutionReport(0, "run run-1 already executed")
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


SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot"


def uses(source: str, names: set[str]) -> bool:
    """Any reference to an attribute in `names`, or getattr(x, "<name>")."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in names:
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and any(isinstance(a, ast.Constant) and a.value in names for a in node.args)
        ):
            return True
    return False


def modules_using(names: set[str]) -> list[str]:
    return sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if uses(p.read_text(), names))


def test_only_the_executor_touches_broker_place() -> None:
    # app gets ApprovedOrders from risk.review; reaching place() any other way (a call, a
    # stored reference, partial, getattr) would skip the kill switch and the ledger
    assert modules_using({"place"}) == ["execution/executor.py"]


@pytest.mark.parametrize(
    "planted",
    ["broker.place(o)", "send = broker.place", "map(broker.place, xs)", 'getattr(broker, "place")(o)'],
)
def test_the_place_scan_catches_each_way_round_it(planted: str) -> None:
    assert uses(planted, {"place"})


def test_no_daily_code_seeds_the_mark() -> None:
    # owner setup steps only: otherwise "no mark: seed it" invites re-seeding a lost history
    assert modules_using({"seed_high_water_mark"}) == []


def test_only_the_remote_ledgers_setup_path_creates_a_ledger() -> None:
    # RemoteLedger.create refuses where a ledger or reference mark ever existed (ADR-0017)
    assert modules_using({"create"}) == ["app/remote_ledger.py"]


def test_an_unknown_broker_order_stops_execute_itself(broker: FakeBroker, ledger: Ledger) -> None:
    # nothing is open in the ledger, but a working order no intent knows about could be a
    # duplicate of ours: execute reconciles first and sends nothing
    broker.market_open = False
    broker.place(approved(Side.BUY, "1"))
    report = run_execute([approved(Side.BUY, "1")], broker, ledger)
    assert report.sent == 0 and report.stopped is not None and "no intent knows" in report.stopped
    assert len(broker.recent_orders()) == 1


def test_reconcile_reports_orders_that_ended_short_of_a_full_fill(broker: FakeBroker, ledger: Ledger) -> None:
    broker.market_open = False
    run_execute([approved(Side.BUY, "1")], broker, ledger)
    broker.cancel(broker.recent_orders()[0].order_id)
    result = reconcile(broker, ledger)
    assert result.clean
    assert len(result.shortfalls) == 1 and "CANCELLED" in result.shortfalls[0]


# --- checkpoints: the app uploads the ledger at each one (Phase 2, ADR-0017) ------------
class Checkpoints:
    """Records what the ledger and the broker looked like at each checkpoint."""

    def __init__(self, broker: FakeBroker, ledger: Ledger, fail_at: int | None = None) -> None:
        self.seen: list[tuple[list[IntentState], int]] = []
        self._broker, self._ledger, self._fail_at = broker, ledger, fail_at

    def __call__(self) -> None:
        states = [i.state for i in self._ledger.open_intents()] + [i.state for i in self._ledger.settled()]
        self.seen.append((states, len(self._broker.recent_orders())))
        if self._fail_at == len(self.seen):
            raise OSError("upload failed")


def test_the_intent_is_checkpointed_before_its_order_is_sent(broker: FakeBroker, ledger: Ledger) -> None:
    saved = Checkpoints(broker, ledger)
    execute(
        [approved(Side.BUY, "1")], broker, ledger, run_id="run-1", now=NOW, may_send=allowed, checkpoint=saved
    )
    assert saved.seen == [
        ([], 0),  # after reconcile
        ([IntentState.INTENDED], 0),  # the intent is saved, nothing sent yet
        ([IntentState.SENT], 1),  # sent, and its broker order ID saved
    ]


def test_a_failed_checkpoint_before_sending_sends_nothing(broker: FakeBroker, ledger: Ledger) -> None:
    saved = Checkpoints(broker, ledger, fail_at=2)
    with pytest.raises(OSError, match="upload failed"):
        execute(
            [approved(Side.BUY, "1")],
            broker,
            ledger,
            run_id="run-1",
            now=NOW,
            may_send=allowed,
            checkpoint=saved,
        )
    assert broker.recent_orders() == []


def test_a_failed_checkpoint_after_sending_stops_before_the_next_order(
    broker: FakeBroker, ledger: Ledger
) -> None:
    saved = Checkpoints(broker, ledger, fail_at=3)
    with pytest.raises(OSError, match="upload failed"):
        execute(
            [approved(Side.BUY, "1"), approved(Side.BUY, "1", SGLN)],
            broker,
            ledger,
            run_id="run-1",
            now=NOW,
            may_send=allowed,
            checkpoint=saved,
        )
    assert len(broker.recent_orders()) == 1


def test_an_unknown_outcome_is_checkpointed(broker: FakeBroker, ledger: Ledger) -> None:
    broker.timeout_after_accepting_next = True
    saved = Checkpoints(broker, ledger)
    execute(
        [approved(Side.BUY, "1")], broker, ledger, run_id="run-1", now=NOW, may_send=allowed, checkpoint=saved
    )
    assert saved.seen[-1] == ([IntentState.UNKNOWN], 1)


def test_an_unexpected_error_is_checkpointed_as_unknown(broker: FakeBroker, ledger: Ledger) -> None:
    broker.fail_next = KeyError("boom")  # type: ignore[assignment] - anything unexpected
    saved = Checkpoints(broker, ledger)
    with pytest.raises(KeyError):
        execute(
            [approved(Side.BUY, "1")],
            broker,
            ledger,
            run_id="run-1",
            now=NOW,
            may_send=allowed,
            checkpoint=saved,
        )
    assert saved.seen[-1] == ([IntentState.UNKNOWN], 0)


def test_a_refused_order_is_checkpointed_as_not_sent(broker: FakeBroker, ledger: Ledger) -> None:
    broker.fail_next = BrokerError("503")
    saved = Checkpoints(broker, ledger)
    execute(
        [approved(Side.BUY, "1")], broker, ledger, run_id="run-1", now=NOW, may_send=allowed, checkpoint=saved
    )
    assert saved.seen[-1] == ([IntentState.NOT_SENT], 0)
