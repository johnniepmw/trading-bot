"""The daily cycle: reconcile, mark, decide, check, execute (ARCHITECTURE.md; ADR-0009).

Wiring only, no trading logic: the decisions are `strategy`, `portfolio` and `risk`; the
sending is `execution`. Every failure stops the cycle with no new orders and an ERROR log
line (the alert). The cycle is safe to run every day and to repeat.

`signal_day` is the latest completed month end, passed every day by the caller's trading
calendar (Phase 2). Each month end is acted on once, like the backtest: the first run after
it checks the signal-day closes against the broker's live prices and stores them; a run
that completes marks the month end acted, and later runs only reconcile and mark. If the
first run was stopped, a later run may act only on the same verified closes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tradingbot.brokers.port import Broker
from tradingbot.data.prices import PriceHistory, validate_history
from tradingbot.domain.market import Bar, Instrument, price_money
from tradingbot.domain.money import Money
from tradingbot.execution.executor import execute, reconcile
from tradingbot.ledger.store import Ledger
from tradingbot.portfolio.weights import daily_returns, orders_for, sleeve_weights, vol_scaled
from tradingbot.risk.limits import Limits
from tradingbot.risk.review import AccountState, review
from tradingbot.strategy.trend import SMA_DAYS, signals

log = logging.getLogger(__name__)

QUOTE_TOLERANCE = Decimal("0.02")  # data source's latest close vs the broker's live price
EQUITY_TOLERANCE = Decimal("0.02")  # broker equity vs cash + holdings at the broker's quotes
MAX_NEW_PEAK = Decimal("0.10")  # a new high-water mark more than 10% above the last is refused
CLOSE_RESCALE_TOLERANCE = Decimal("0.005")  # adjusted histories rescale on ex-dividend dates
MAX_SIGNAL_AGE = timedelta(days=35)  # an older month end is a calendar bug, not a decision
HISTORY_CALENDAR_DAYS = 450  # enough for 210 trading days plus the 60-day volatility window
# risk vetoes that concern the whole account: these are alerts, not routine shrinks
ACCOUNT_VETOES = ("no high-water mark", "no day-start equity", "drawdown stop", "daily loss limit")


class CycleStopError(Exception):
    """A check failed: no new orders this run."""


@dataclass(frozen=True)
class CycleConfig:
    universe: Mapping[Instrument, Decimal]  # every instrument the strategy trades, with its quantity step
    limits: Limits = field(default_factory=Limits)


@dataclass(frozen=True)
class CycleReport:
    sent: int
    stopped: str | None


def run_cycle(  # noqa: PLR0913 - the composition root passes every dependency explicitly
    *,
    broker: Broker,
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    signal_day: date | None,
    now: datetime,
    run_id: str,
    may_send: Callable[[], bool],
) -> CycleReport:
    try:
        report = _cycle(broker, ledger, history, config, signal_day, now, run_id, may_send)
    except CycleStopError as stop:
        log.error("cycle stopped: %s", stop)
        return CycleReport(0, str(stop))
    except Exception as error:  # fail closed: any error means no new orders, plus an alert
        reason = f"{type(error).__name__}: {error}"
        log.exception("cycle stopped: %s", reason)
        return CycleReport(0, reason)
    if report.stopped:
        log.error("cycle stopped: %s", report.stopped)
    return report


def _cycle(  # noqa: PLR0913, PLR0917 - see run_cycle
    broker: Broker,
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    signal_day: date | None,
    now: datetime,
    run_id: str,
    may_send: Callable[[], bool],
) -> CycleReport:
    if now.tzinfo is not UTC:
        raise CycleStopError(f"the clock must be UTC, got {now!r}")
    if signal_day is not None and signal_day >= now.date():
        raise CycleStopError(f"signal day {signal_day} is not before today ({now.date()})")
    if signal_day is not None and now.date() - signal_day > MAX_SIGNAL_AGE:
        raise CycleStopError(f"signal day {signal_day} is too old")
    reconciled = reconcile(broker, ledger)
    for shortfall in reconciled.shortfalls:
        log.error("order ended short: %s (the month end's decision stands until the next one)", shortfall)
    if not reconciled.clean:
        raise CycleStopError("not reconciled: " + "; ".join(reconciled.problems))

    holdings = _holdings(broker, config.universe)
    account = broker.account()
    held_quotes = {i: broker.quote(i) for i, q in holdings.items() if q > 0}
    _check_equity(account.cash, account.equity, holdings, held_quotes)
    mark = ledger.high_water_mark()
    if mark is None:
        raise CycleStopError("no high-water mark: the owner must seed it (setup step)")
    if account.equity.amount > mark.amount * (1 + MAX_NEW_PEAK):
        raise CycleStopError(f"implausible new peak: {account.equity} against a mark of {mark}")
    day_start = ledger.previous_day_equity(now.date())
    ledger.update_high_water_mark(account.equity, now)
    ledger.record_equity(now.date(), account.equity)
    mark = ledger.high_water_mark()
    if signal_day is None or ledger.signal_acted(signal_day):
        return CycleReport(0, None)

    bars = _checked_history(history, config.universe, signal_day, now.date())
    quotes = {i: broker.quote(i) for i in config.universe}
    _cross_check(bars, quotes)
    _verify_signal_closes(ledger, bars, signal_day)
    weights = sleeve_weights(signals(bars, signal_day))
    weights = vol_scaled(weights, {i: daily_returns(b, signal_day) for i, b in bars.items()})
    proposed = orders_for(
        weights, holdings=holdings, prices=quotes, equity=account.equity, steps=config.universe
    )
    state = AccountState(
        equity=account.equity,
        cash=account.cash,
        high_water_mark=mark,
        day_start_equity=day_start,
        holdings=holdings,
        quotes=quotes,
        quantity_steps=config.universe,
    )
    decisions = review(proposed, state, config.limits)
    for decision in decisions:
        if decision.approved is None:
            alert = any(veto in decision.reason for veto in ACCOUNT_VETOES)
            log.log(
                logging.ERROR if alert else logging.WARNING,
                "%s %s: %s",
                decision.order.side,
                decision.order.instrument.ticker,
                decision.reason,
            )
    approved = [d.approved for d in decisions if d.approved is not None]
    result = execute(approved, broker, ledger, run_id=run_id, now=now, may_send=may_send)
    if result.stopped is None:
        ledger.mark_signal_acted(signal_day)
    return CycleReport(result.sent, result.stopped)


def _holdings(broker: Broker, universe: Mapping[Instrument, Decimal]) -> dict[Instrument, Decimal]:
    holdings = {i: Decimal(0) for i in universe}
    seen: set[Instrument] = set()
    for position in broker.positions():
        if position.instrument not in holdings:
            raise CycleStopError(f"broker position {position.instrument.ticker} maps to no instrument")
        if position.instrument in seen:
            raise CycleStopError(f"broker reports {position.instrument.ticker} twice")
        seen.add(position.instrument)
        holdings[position.instrument] = position.quantity
    return holdings


def _check_equity(
    cash: Money, equity: Money, holdings: Mapping[Instrument, Decimal], quotes: Mapping[Instrument, Money]
) -> None:
    """The broker's equity must match cash plus holdings at its own quotes, before it can
    move the high-water mark or the day start, or size anything."""
    for instrument, quote in quotes.items():
        if quote.currency is not instrument.currency or quote.amount <= 0:
            raise CycleStopError(f"equity check failed: bad broker quote {quote} for {instrument.ticker}")
    computed = cash.amount + sum(
        (q * quotes[i].to_gbp().amount for i, q in holdings.items() if q > 0), Decimal(0)
    )
    if computed <= 0 or abs(equity.amount / computed - 1) > EQUITY_TOLERANCE:
        raise CycleStopError(f"equity check failed: broker says {equity}, cash and holdings give {computed}")


def _checked_history(
    history: PriceHistory, universe: Mapping[Instrument, Decimal], signal_day: date, today: date
) -> dict[Instrument, Sequence[Bar]]:
    """Every instrument's validated bars up to yesterday, or a stop. All or nothing: a fund
    with bad or short data is never just dropped (that would sell it)."""
    start = signal_day - timedelta(days=HISTORY_CALENDAR_DAYS)
    bars = {}
    for instrument in universe:
        series = validate_history(
            instrument, history.daily_bars(instrument, start, today - timedelta(days=1))
        )
        upto_signal = [b for b in series if b.day <= signal_day]
        if not upto_signal or upto_signal[-1].day != signal_day:
            raise CycleStopError(f"stale data: {instrument.ticker} has no bar for {signal_day}")
        if len(upto_signal) < SMA_DAYS:
            raise CycleStopError(f"history too short for {instrument.ticker}: {len(upto_signal)} bars")
        bars[instrument] = series
    return bars


def _cross_check(bars: Mapping[Instrument, Sequence[Bar]], quotes: Mapping[Instrument, Money]) -> None:
    """The data source's latest close must be within QUOTE_TOLERANCE of the broker's live
    price, so a wrong third-party price can't flip a signal."""
    for instrument, series in bars.items():
        close = price_money(instrument, series[-1].close)
        quote = quotes[instrument]
        if quote.currency is not close.currency or abs(quote.amount / close.amount - 1) > QUOTE_TOLERANCE:
            raise CycleStopError(
                f"price cross-check failed for {instrument.ticker}: data {close}, broker {quote}"
            )


def _verify_signal_closes(ledger: Ledger, bars: Mapping[Instrument, Sequence[Bar]], signal_day: date) -> None:
    """On the first run after the month end the latest close is the signal-day close, and it
    has just passed the cross-check: store it. A later run may only act on the same closes
    (within CLOSE_RESCALE_TOLERANCE). If they were never verified, the month end is skipped:
    one alert, then later runs only reconcile and mark until the next month end."""
    closes = {
        i: price_money(i, next(b for b in reversed(s) if b.day == signal_day).close) for i, s in bars.items()
    }
    if all(s[-1].day == signal_day for s in bars.values()):
        ledger.store_verified_closes(signal_day, closes)
        return
    verified = ledger.verified_closes(signal_day)
    if set(verified) != set(closes) or any(
        abs(closes[i].amount / verified[i].amount - 1) > CLOSE_RESCALE_TOLERANCE for i in closes
    ):
        ledger.mark_signal_acted(signal_day)
        raise CycleStopError(
            f"signal-day closes for {signal_day} not verified against the broker, or changed since:"
            " month end skipped until the next one"
        )
