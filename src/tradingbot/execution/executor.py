"""Send approved orders, ledger first; reconcile the ledger with the broker (ADR-0016).

`execute` accepts only ApprovedOrder. Before each order it asks `may_send` (the app wires
the `new-orders` kill switch here, read fresh each time), records the intent, then sends.
A timeout leaves the intent UNKNOWN and stops the run: it is never resent. `reconcile`
runs at the start of every run and must be clean before any new order.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from tradingbot.brokers.port import Broker, BrokerError, BrokerTimeoutError, OrderStatus
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.ledger.store import IntentState, Ledger

_SETTLED = {
    OrderStatus.FILLED: IntentState.FILLED,
    OrderStatus.PARTIALLY_FILLED: IntentState.PARTIALLY_FILLED,
    OrderStatus.REJECTED: IntentState.REJECTED,
    OrderStatus.CANCELLED: IntentState.CANCELLED,
}


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
        ledger.mark_sent(intent, order_id)
        sent += 1
    return ExecutionReport(sent, None)


def reconcile(broker: Broker, ledger: Ledger) -> ReconcileResult:
    """Settle every open intent from the broker's records. Problems block new orders."""
    result = ReconcileResult()
    linked = ledger.linked_order_ids()
    unlinked = [o for o in broker.recent_orders() if o.order_id not in linked]
    for intent in ledger.open_intents():
        if intent.state is IntentState.SENT and intent.broker_order_id is not None:
            status = broker.order(intent.broker_order_id).status
            if status in _SETTLED:
                ledger.settle(intent.intent_id, _SETTLED[status])
            else:
                result.problems.append(f"intent {intent.intent_id}: broker order still {status}")
            continue
        match = next((o for o in unlinked if o.order == intent.order), None)
        if match is not None:
            unlinked.remove(match)
            if match.status in _SETTLED:
                ledger.settle(intent.intent_id, _SETTLED[match.status], match.order_id)
            else:
                ledger.mark_sent(intent.intent_id, match.order_id)
                result.problems.append(f"intent {intent.intent_id}: broker order still {match.status}")
        elif intent.state is IntentState.INTENDED:
            ledger.settle(intent.intent_id, IntentState.NOT_SENT)  # crashed before sending
        else:
            result.problems.append(f"intent {intent.intent_id}: outcome unknown and no matching broker order")
    return result
