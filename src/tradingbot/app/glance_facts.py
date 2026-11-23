"""The glance's figures, read from the ledger a run just wrote (daily email and dashboard).

Shared by the email (after each run) and the dashboard (whenever the owner looks), so the two
can never disagree. Read-only: it never changes the ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from tradingbot.app.calendar import TradingCalendar
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger, LedgerError
from tradingbot.report.glance import GlanceInput
from tradingbot.risk.limits import Limits


def _gbp(amount: Decimal) -> Money:
    return Money(amount, Currency.GBP)


def glance_input(  # noqa: PLR0913 - the run's facts and the config the lines compare with
    ledger: Ledger,
    run_id: str,
    *,
    today: date,
    calendar: TradingCalendar,
    universe: Mapping[Instrument, Decimal],
    limits: Limits,
    now: datetime,
    stopped: str | None = None,
    in_progress: bool = False,
) -> GlanceInput:
    """The glance's figures from the ledger. `stopped` is the run's own stop reason, which
    wins over the run record (a final save that failed leaves the record "completed"). A run
    with no end is "running" while the lock is held (`in_progress`), otherwise "died"."""
    run = next((r for r in ledger.runs() if r.run_id == run_id), None)
    outcome: str
    reason: str | None
    if stopped:
        outcome, reason = "stopped", stopped
    elif run is None:
        outcome, reason = "stopped", "no run record"
    elif run.ended_at is None:
        outcome = "running" if in_progress else "died"
        reason = None if in_progress else "did not finish (a timeout or crash)"
    else:
        outcome, reason = run.outcome or "stopped", run.reason
    try:
        mark = ledger.high_water_mark()
    except LedgerError:
        mark = None
    try:
        snapshot = ledger.snapshot(run_id)
    except LedgerError:
        snapshot = None
    held = [p for p in snapshot.positions if p.quantity > 0] if snapshot else []
    preview = ledger.preview(run_id)
    open_intents = ledger.open_intents()
    intents = open_intents + ledger.settled()
    sent_today = sum(
        1 for i in intents if i.created_at.date() == today and i.state is not IntentState.NOT_SENT
    )
    awaiting = [i for i in open_intents if i.run_id == run_id and i.state is IntentState.SENT]
    return GlanceInput(
        as_of=(run.ended_at or run.started_at) if run else now,
        outcome=outcome,
        reason=reason,
        equity=ledger.equity_on(today),
        day_start=ledger.previous_day_equity(today),
        mark=mark,
        seed=ledger.first_high_water_mark(),
        cash=snapshot.cash if snapshot else None,
        invested=_gbp(sum((p.value_gbp.amount for p in held if p.value_gbp), Decimal(0)))
        if snapshot
        else None,
        funds_held=len(held),
        funds_total=len(universe),
        snapshot_checked=snapshot.equity_checked if snapshot else None,
        sent_today=sent_today,
        awaiting_fill=len(awaiting),
        open_orders=len(open_intents) - len(awaiting),
        next_month_end=calendar.next_month_end(today),
        in_trend=sum(1 for p in preview if p.in_trend) if preview else None,
        daily_loss_limit=limits.daily_loss,
        drawdown_halve=limits.drawdown_halve,
        drawdown_stop=limits.drawdown_stop,
    )
