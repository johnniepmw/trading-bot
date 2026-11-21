"""Send approved orders, ledger first; reconcile the ledger with the broker (ADR-0016).

`execute` accepts only ApprovedOrder and refuses to run while any intent is open, so
reconcile must have settled everything first. Before each order it asks `may_send` (the
app wires the `new-orders` kill switch here, read fresh each time), records the intent,
then sends. Any outcome that isn't certain leaves the intent UNKNOWN and stops the run:
it is never resent. `reconcile` runs at the start of every run.
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
    problems: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems


def execute(  # noqa: PLR0913 - the order path's explicit dependencies
    orders: Sequence[ApprovedOrder],
    broker: Broker,
    ledger: Ledger,
    *,
    run_id: str,
    now: datetime,
    may_send: Callable[[], bool],
) -> ExecutionReport:
    for order in orders:
        if not isinstance(order, ApprovedOrder):
            raise TypeError("execute accepts only ApprovedOrder (risk.review creates them)")
    if ledger.open_intents():
        return ExecutionReport(0, "open intents: reconcile first")
    sent = 0
    for order in orders:
        if not may_send():
            return ExecutionReport(sent, "kill switch off")
        intent = ledger.record_intent(run_id, order.order, now)
        try:
            order_id = broker.place(order)
        except BrokerTimeoutError:
            ledger.mark_unknown(intent)
            return ExecutionReport(sent, "broker timeout: outcome unknown")
        except BrokerError as error:
            ledger.settle(intent, IntentState.NOT_SENT)
            return ExecutionReport(sent, f"broker error: {error}")
        except BaseException:
            ledger.mark_unknown(intent)  # anything unexpected: the order may have gone out
            raise
        try:
            ledger.mark_sent(intent, order_id)
        except BaseException:
            ledger.mark_unknown(intent)
            raise
        sent += 1
    return ExecutionReport(sent, None)


def _settle(ledger: Ledger, intent: Intent, order: BrokerOrder, result: ReconcileResult) -> None:
    if order.status in _SETTLED:
        ledger.settle(intent.intent_id, _SETTLED[order.status], order.order_id, order.filled_quantity)
    else:
        if intent.broker_order_id is None:
            ledger.mark_sent(intent.intent_id, order.order_id)
        result.problems.append(f"intent {intent.intent_id}: broker order still {order.status}")


def reconcile(broker: Broker, ledger: Ledger) -> ReconcileResult:
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
