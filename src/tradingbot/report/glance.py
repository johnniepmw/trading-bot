"""The glance: seven lines on the account's state, each OK, WARN or STOP (daily email spec,
docs/exec-plans/active/2026-11-23-daily-email.md). Pure: built from figures the job reads
from the ledger it just wrote, so it can never disagree with the decision record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from tradingbot.domain.money import Money

DRAWDOWN_WARN = Decimal("0.05")


class Level(StrEnum):
    OK = "OK"
    WARN = "!"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class Line:
    level: Level
    text: str


@dataclass(frozen=True, slots=True)
class GlanceInput:
    as_of: datetime  # UTC, the run's end
    outcome: str  # "completed"; "running" (amber); anything else (stopped, died): red
    reason: str | None  # why it stopped
    equity: Money | None  # recorded today
    day_start: Money | None
    mark: Money | None  # high-water mark
    seed: Money | None  # the equity setup seeded
    cash: Money | None
    invested: Money | None  # held positions at the run's quotes (GBP)
    funds_held: int
    funds_total: int
    snapshot_checked: bool | None  # None: no snapshot this run
    sent_today: int
    awaiting_fill: int  # this run's orders the broker hasn't filled yet (normal before the open)
    open_orders: int  # left open by earlier runs, or of unknown outcome
    next_month_end: date
    in_trend: int | None  # funds in trend on today's preview; None: no preview
    daily_loss_limit: Decimal  # config: the daily-loss pause, as a fraction
    drawdown_halve: Decimal
    drawdown_stop: Decimal


def _gbp(money: Money) -> str:
    return f"£{money.amount:,.2f}"


def _signed(amount: Decimal) -> str:
    return f"{'+' if amount >= 0 else '-'}£{abs(amount):,.2f}"


def _pct(fraction: Decimal) -> str:
    return f"{fraction * 100:+.1f}%"


def _run(g: GlanceInput) -> Line:
    when = g.as_of.strftime("%a %d %b %H:%M UTC")
    if g.outcome == "completed":
        return Line(Level.OK, f"Run: completed {when}")
    if g.outcome == "running":
        return Line(Level.WARN, f"Run: in progress since {when}")
    if g.outcome == "died":
        return Line(Level.STOP, f"Run: did not finish (started {when}); the next run reports it")
    return Line(Level.STOP, f"Run: STOPPED {when}: {g.reason or 'no reason given'}")


def _today(g: GlanceInput) -> Line:
    if g.equity is None:
        return Line(Level.STOP, "Today: no equity recorded")
    if g.day_start is None or g.day_start.amount <= 0:
        return Line(Level.STOP, f"Today: {_gbp(g.equity)}; no day-start equity (buys paused)")
    change = g.equity.amount - g.day_start.amount
    fraction = change / g.day_start.amount
    level = Level.OK if change >= 0 else Level.STOP if -fraction >= g.daily_loss_limit else Level.WARN
    return Line(level, f"Today: {_gbp(g.equity)} ({_signed(change)}, {_pct(fraction)})")


def _drawdown(g: GlanceInput) -> Line:
    if g.equity is None or g.mark is None or g.mark.amount <= 0:
        return Line(Level.STOP, "Drawdown: unknown (no equity or high-water mark)")
    below = g.mark.amount - g.equity.amount
    fraction = max(below, Decimal(0)) / g.mark.amount
    gap = Money(max(below, Decimal(0)), g.mark.currency)
    text = f"Drawdown: {fraction * 100:.1f}% ({_gbp(gap)}) below the peak of {_gbp(g.mark)}"
    if fraction >= g.drawdown_stop:
        return Line(Level.STOP, text + "; buy stop")
    if fraction >= g.drawdown_halve:
        return Line(Level.STOP, text + "; positions halved")
    return Line(Level.WARN if fraction >= DRAWDOWN_WARN else Level.OK, text)


def _since_setup(g: GlanceInput) -> Line:
    if g.equity is None or g.seed is None or g.seed.amount <= 0:
        return Line(Level.WARN, "Since setup: unknown")
    change = g.equity.amount - g.seed.amount
    level = Level.OK if change >= 0 else Level.WARN
    return Line(level, f"Since setup: {_signed(change)} ({_pct(change / g.seed.amount)}) on {_gbp(g.seed)}")


def _holdings(g: GlanceInput) -> Line:
    if g.snapshot_checked is None or g.cash is None or g.invested is None:
        return Line(Level.WARN, "Holdings: no snapshot this run")
    total = g.cash.amount + g.invested.amount
    share = g.invested.amount / total if total > 0 else Decimal(0)
    funds = f"{g.funds_held} of {g.funds_total} funds"
    text = f"Holdings: {share * 100:.0f}% invested in {funds}; cash {_gbp(g.cash)}"
    if not g.snapshot_checked:
        return Line(Level.STOP, text + " (unverified: the equity check failed)")
    return Line(Level.OK, text)


def _orders(g: GlanceInput) -> Line:
    today = f"{g.sent_today} sent today ({g.awaiting_fill} awaiting fill)"
    text = f"Orders: {today}; {g.open_orders} open from earlier"
    return Line(Level.STOP if g.open_orders else Level.OK, text)


def _month_end(g: GlanceInput) -> Line:
    when = g.next_month_end.strftime("%a %d %b")
    if g.in_trend is None:
        return Line(Level.WARN, f"Next month end: {when}; no preview today")
    return Line(
        Level.OK,
        f"Next month end: {when}; {g.in_trend} of {g.funds_total} funds in trend today ({g.funds_held} held)",
    )


def glance(g: GlanceInput) -> list[Line]:
    return [_run(g), _today(g), _drawdown(g), _since_setup(g), _holdings(g), _orders(g), _month_end(g)]


def render(lines: list[Line], *, as_of: datetime, environment: str) -> tuple[str, str]:
    """Subject and plain-text body. No links (never a pause link: monitoring AC10)."""
    worst = (
        Level.STOP
        if any(x.level is Level.STOP for x in lines)
        else (Level.WARN if any(x.level is Level.WARN for x in lines) else Level.OK)
    )
    label = {Level.OK: "all OK", Level.WARN: "check", Level.STOP: "ACTION"}[worst]
    subject = f"Trading bot ({environment}) {as_of:%a %d %b}: {label}"
    width = max(len(f"[{level}]") for level in Level)
    body = "\n".join(f"{f'[{x.level}]':<{width}} {x.text}" for x in lines)
    footer = f"\n\nAs of {as_of:%Y-%m-%d %H:%M} UTC. Figures from the bot's ledger; details in Cloud Logging."
    return subject, body + footer
