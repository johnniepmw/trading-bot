"""Send approved orders, ledger first; reconcile the ledger with the broker (ADR-0016).

`execute` accepts only ApprovedOrder. It runs `reconcile` itself and sends nothing unless
that is clean (no open intent, no working broker order the ledger doesn't know), and it
refuses a run ID that already recorded orders, so a retry can't send a batch twice.
Before each order it asks `may_send` (the app wires the `new-orders` kill switch here,
read fresh each time), records the intent, asks again (saving it can take a while), then
sends. Any outcome that isn't certain leaves the intent UNKNOWN and stops the run: it is
never resent. `reconcile` runs at the start of every run.

`checkpoint` saves the ledger remotely (ADR-0017): after reconcile, after each intent is
recorded (before its order is sent) and after each intent changes state. If it raises, the
error propagates and nothing more is sent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradingbot.brokers.port import (
    TERMINAL,
    Broker,
    BrokerError,
    BrokerOrder,
    BrokerReader,
    BrokerTimeoutError,
    OrderStatus,
)
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.ledger.store import Intent, IntentState, Ledger

_SETTLED = {
    OrderStatus.FILLED: IntentState.FILLED,
    OrderStatus.REJECTED: IntentState.REJECTED,
    OrderStatus.CANCELLED: IntentState.CANCELLED,
}
# A broker order counts as a candidate for an intent if the broker created it no earlier
# than this before the intent was recorded (clock differences between us and the broker).
CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class ExecutionReport:
    sent: int
    stopped: str | None  # why the run stopped early, if it did


@dataclass
class ReconcileResult:
    problems: list[str] = field(default_factory=list)  # block new orders
    shortfalls: list[str] = field(default_factory=list)  # settled short of a full fill: alert, don't block

    @property
    def clean(self) -> bool:
        return not self.problems


def execute(  # noqa: PLR0913, PLR0911 - the order path's explicit dependencies; one return per way it stops
    orders: Sequence[ApprovedOrder],
    broker: Broker,
    ledger: Ledger,
    *,
    run_id: str,
    now: datetime,
    may_send: Callable[[], bool],
    checkpoint: Callable[[], None],
) -> ExecutionReport:
    for order in orders:
        if not isinstance(order, ApprovedOrder):
            raise TypeError("execute accepts only ApprovedOrder (risk.review creates them)")
    if ledger.has_run(run_id):
        return ExecutionReport(0, f"run {run_id} already executed")
    reconciled = reconcile(broker, ledger)
    checkpoint()
    if not reconciled.clean:
        return ExecutionReport(0, "not reconciled: " + "; ".join(reconciled.problems))
    sent = 0
    for order in orders:
        if not may_send():
            return ExecutionReport(sent, "kill switch off")
        intent = ledger.record_intent(run_id, order.order, now)
        checkpoint()  # the intent is saved remotely before the order can go out
        if not may_send():  # read again: the save can take a while
            ledger.settle(intent, IntentState.NOT_SENT)
            checkpoint()
            return ExecutionReport(sent, "kill switch off")
        try:
            order_id = broker.place(order)
        except BrokerTimeoutError:
            ledger.mark_unknown(intent)
            checkpoint()
            return ExecutionReport(sent, "broker timeout: outcome unknown")
        except BrokerError as error:
            ledger.settle(intent, IntentState.NOT_SENT)
            checkpoint()
            return ExecutionReport(sent, f"broker error: {error}")
        except BaseException:
            ledger.mark_unknown(intent)  # anything unexpected: the order may have gone out
            checkpoint()
            raise
        try:
            ledger.mark_sent(intent, order_id)
        except BaseException:
            ledger.mark_unknown(intent)
            checkpoint()
            raise
        checkpoint()
        sent += 1
    return ExecutionReport(sent, None)


def _settle(ledger: Ledger, intent: Intent, order: BrokerOrder, result: ReconcileResult) -> None:
    if order.status in _SETTLED:
        ledger.settle(intent.intent_id, _SETTLED[order.status], order.order_id, order.filled_quantity)
        if order.status is not OrderStatus.FILLED:
            o = order.order
            result.shortfalls.append(
                f"intent {intent.intent_id}: {o.side} {o.instrument.ticker} ended {order.status}"
                f" with {order.filled_quantity} of {o.quantity} filled"
            )
    else:
        if intent.broker_order_id is None:
            ledger.mark_sent(intent.intent_id, order.order_id)
        result.problems.append(f"intent {intent.intent_id}: broker order still {order.status}")


def reconcile(broker: BrokerReader, ledger: Ledger) -> ReconcileResult:
    """Settle every open intent from the broker's records. Problems block new orders."""
    result = ReconcileResult()
    linked = ledger.linked_order_ids()
    unlinked = [o for o in broker.recent_orders() if o.order_id not in linked]
    for intent in ledger.open_intents():
        if intent.broker_order_id is not None:
            _settle(ledger, intent, broker.order(intent.broker_order_id), result)
            continue
        candidates = [
            o for o in unlinked if o.order == intent.order and o.created_at >= intent.created_at - CLOCK_SKEW
        ]
        if len(candidates) > 1:
            result.problems.append(f"intent {intent.intent_id}: {len(candidates)} broker orders could be it")
        elif candidates:
            unlinked.remove(candidates[0])
            _settle(ledger, intent, candidates[0], result)
        elif intent.state is IntentState.INTENDED:
            ledger.settle(intent.intent_id, IntentState.NOT_SENT)  # crashed before sending
        else:
            result.problems.append(f"intent {intent.intent_id}: outcome unknown and no matching broker order")
    for order in unlinked:
        if order.status not in TERMINAL:
            result.problems.append(
                f"broker order {order.order_id} is {order.status} and no intent knows about it"
            )
    return result
