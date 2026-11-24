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
from types import MappingProxyType

from tradingbot.app.alerts import drawdown_crossings
from tradingbot.brokers.port import Broker, BrokerError, BrokerReader, NoQuoteError
from tradingbot.data.prices import DataError, PriceHistory, validate_history
from tradingbot.domain.market import BENCHMARK_ONLY, Bar, Instrument, base_ticker, price_money
from tradingbot.domain.money import Currency, Money
from tradingbot.execution.executor import execute, reconcile
from tradingbot.ledger.store import (
    DecisionRecord,
    Ledger,
    LevelRecord,
    PositionRecord,
    PreviewRecord,
    SignalRecord,
)
from tradingbot.portfolio.weights import daily_returns, orders_for, sleeve_weights, vol_scaled
from tradingbot.risk.limits import Limits
from tradingbot.risk.review import AccountState, Decision, review
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
    benchmark: Instrument | None = None  # compared against on the dashboard, never traded

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "universe", MappingProxyType(dict(self.universe))
        )  # can't change after the check
        benchmarks = sorted(i.ticker for i in self.universe if base_ticker(i.ticker) in BENCHMARK_ONLY)
        if benchmarks:
            raise ValueError(
                f"{', '.join(benchmarks)} is a benchmark only and can't be in the trading universe"
            )
        if self.benchmark is not None and base_ticker(self.benchmark.ticker) not in BENCHMARK_ONLY:
            raise ValueError(f"{self.benchmark.ticker} isn't a benchmark-only instrument")


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
    clock: Callable[[], datetime],
    run_id: str,
    may_send: Callable[[], bool],
    checkpoint: Callable[[], None],
    version: str,
) -> CycleReport:
    """`clock` gives the current UTC time (read at the start and again at the end).
    `checkpoint` saves the ledger remotely: around each order (in `execute`) and once at the
    end of every run, whatever happened."""
    recorded = False
    now: datetime | None = None
    try:
        now = clock()
        if now.tzinfo is UTC:  # a run with a wrong clock can't be recorded; _cycle stops it below
            _start_record(ledger, run_id, now, version)
            recorded = True
        report = _cycle(broker, ledger, history, config, signal_day, now, run_id, may_send, checkpoint)
        if report.stopped:
            log.error("cycle stopped: %s", report.stopped)
    except CycleStopError as stop:
        log.error("cycle stopped: %s", stop)
        report = CycleReport(0, str(stop))
    except Exception as error:  # fail closed: any error means no new orders, plus an alert
        reason = f"{type(error).__name__}: {error}"
        log.exception("cycle stopped: %s", reason)
        report = CycleReport(0, reason)
    if recorded and now is not None:
        _finish_record(ledger, history, config, run_id=run_id, started=now, clock=clock, report=report)
    try:  # every run saves at the end: the mark, equity, settlements and the run record (ADR-0017)
        checkpoint()
    except Exception as error:  # noqa: BLE001 - any failure to save is a stop with an alert
        reason = f"ledger not saved: {type(error).__name__}: {error}"
        log.error("cycle stopped: %s", reason)
        report = CycleReport(report.sent, reason)
    return report


@dataclass(frozen=True)
class DryRunReport:
    would_send: int  # orders the risk layer approved, which a run would send now
    stopped: str | None
    business: bool = False  # stopped for a reason the world gave (BUSINESS_STOPS), not a bug


# Stops the account or the outside world causes (a cycle stop, stale or missing data, the
# broker), as opposed to a bug in the code. A deploy's check accepts these (ADR-0019
# amendment). LedgerError is not here: most of it guards programming invariants.
BUSINESS_STOPS: tuple[type[Exception], ...] = (CycleStopError, BrokerError, DataError)


def dry_run_cycle(  # noqa: PLR0913 - as run_cycle, minus sending
    *,
    broker: BrokerReader,
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    signal_day: date | None,
    clock: Callable[[], datetime],
    run_id: str,
) -> DryRunReport:
    """The cycle up to sending, on a scratch ledger (Phase 2 AC6). Takes a `BrokerReader`, so
    it can't send; logs its own outcome line, never `cycle stopped`, and alerts nothing (the
    real run does that)."""
    try:
        decisions = _decide(
            broker, ledger, history, config, signal_day, clock(), run_id, alert=logging.WARNING
        )
    except Exception as error:  # noqa: BLE001 - every stop is reported, never raised
        reason = str(error) if isinstance(error, CycleStopError) else f"{type(error).__name__}: {error}"
        log.warning("dry run stopped: %s", reason)
        return DryRunReport(0, reason, business=isinstance(error, BUSINESS_STOPS))
    would_send = sum(d.approved is not None for d in decisions or [])
    log.info("dry run: %d order(s) would be sent", would_send)
    return DryRunReport(would_send, None)


def _start_record(ledger: Ledger, run_id: str, now: datetime, version: str) -> None:
    """Report every earlier run that never finished, then record this one. A run ID that was
    used before (a retry of the same execution) is refused, so no attempt's record or
    decisions can be mixed with another's; the composition root gives each attempt its own ID."""
    for unfinished in ledger.unfinished_runs():
        log.error("run %s started at %s never finished", unfinished.run_id, unfinished.started_at.isoformat())
        ledger.mark_run_died(unfinished.run_id)
    if any(r.run_id == run_id for r in ledger.runs()):
        raise CycleStopError(f"run id {run_id} already used: each attempt needs a new run id")
    ledger.start_run(run_id, now, version)


def _finish_record(  # noqa: PLR0913 - the run's outcome and what the preview needs
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    *,
    run_id: str,
    started: datetime,
    clock: Callable[[], datetime],
    report: CycleReport,
) -> None:
    """Close the run record, then store the month-end preview. Neither can change an order:
    the orders are already decided, and a failure here (the clock included) only logs."""
    try:
        ledger.finish_run(
            run_id, clock(), "completed" if report.stopped is None else "stopped", report.stopped
        )
    except Exception:
        log.exception("could not record the end of run %s", run_id)
    try:
        preview, levels = _preview(history, config, started.date(), anchor=ledger.first_level_day())
        ledger.record_preview(run_id, preview)
        ledger.record_levels(run_id, levels)
    except Exception as error:  # noqa: BLE001 - the preview is informational only (monitoring AC3)
        log.warning("month-end preview failed: %s: %s", type(error).__name__, error)


def _preview(
    history: PriceHistory, config: CycleConfig, today: date, *, anchor: date | None = None
) -> tuple[list[PreviewRecord], list[LevelRecord]]:
    """What the month-end rule would do on the latest closes, and the closes and 210-day
    averages it saw, plus the benchmark's close (dashboard slice 2). Never used for orders."""
    start = today - timedelta(days=HISTORY_CALENDAR_DAYS)
    bars = {
        i: validate_history(i, history.daily_bars(i, start, today - timedelta(days=1)))
        for i in config.universe
    }
    as_of = min(series[-1].day for series in bars.values())
    in_trend = signals(bars, as_of)
    weights = vol_scaled(sleeve_weights(in_trend), {i: daily_returns(b, as_of) for i, b in bars.items()})
    preview = [PreviewRecord(as_of, i, in_trend[i], weights[i]) for i in config.universe]
    anchor = anchor or as_of  # the first levels day anchors every later run
    levels = [_level(i, bars[i], as_of, anchor) for i in config.universe]
    if config.benchmark is not None:
        try:
            levels.append(_benchmark_level(history, config.benchmark, as_of, anchor))
        except Exception as error:  # noqa: BLE001 - the preview stays without the benchmark
            log.warning("benchmark close unavailable: %s: %s", type(error).__name__, error)
    return preview, levels


def _close_on(bars: Sequence[Bar], day: date) -> float | None:
    """The close on `day` (or the last before it) from one fetch, if the bars reach back."""
    before = [b for b in bars if b.day <= day]
    return before[-1].close if before and bars[0].day <= day else None


def _level(instrument: Instrument, bars: Sequence[Bar], as_of: date, anchor: date) -> LevelRecord:
    closes = [b.close for b in bars if b.day <= as_of]
    window = closes[-SMA_DAYS:]
    average = sum(window) / SMA_DAYS if len(window) == SMA_DAYS else None
    return LevelRecord(as_of, instrument, closes[-1], average, _close_on(bars, anchor))


BENCHMARK_LOOK_BACK = timedelta(days=14)  # the benchmark's latest close on or before the preview day


def _benchmark_level(history: PriceHistory, benchmark: Instrument, as_of: date, anchor: date) -> LevelRecord:
    start = min(anchor - BENCHMARK_LOOK_BACK, as_of - BENCHMARK_LOOK_BACK)
    bars = [
        b for b in validate_history(benchmark, history.daily_bars(benchmark, start, as_of)) if b.day <= as_of
    ]
    if not bars or bars[-1].day < as_of - BENCHMARK_LOOK_BACK:
        raise DataError(f"no {benchmark.ticker} close in the {BENCHMARK_LOOK_BACK.days} days to {as_of}")
    return LevelRecord(bars[-1].day, benchmark, bars[-1].close, None, _close_on(bars, anchor))


def _cycle(  # noqa: PLR0913, PLR0917 - see run_cycle
    broker: Broker,
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    signal_day: date | None,
    now: datetime,
    run_id: str,
    may_send: Callable[[], bool],
    checkpoint: Callable[[], None],
) -> CycleReport:
    decisions = _decide(broker, ledger, history, config, signal_day, now, run_id, alert=logging.ERROR)
    if decisions is None or signal_day is None:
        return CycleReport(0, None)
    approved = [d.approved for d in decisions if d.approved is not None]
    result = execute(
        approved, broker, ledger, run_id=run_id, now=now, may_send=may_send, checkpoint=checkpoint
    )
    if result.stopped is None:
        ledger.mark_signal_acted(signal_day)
    return CycleReport(result.sent, result.stopped)


def _decide(  # noqa: PLR0913, PLR0917 - see run_cycle
    broker: BrokerReader,
    ledger: Ledger,
    history: PriceHistory,
    config: CycleConfig,
    signal_day: date | None,
    now: datetime,
    run_id: str,
    *,
    alert: int,
) -> list[Decision] | None:
    """Everything up to sending: the risk layer's decisions, or None when there is no month end
    to act on. Takes a `BrokerReader`, so nothing here can place an order. `alert` is the
    level for account-level problems: ERROR in a run, WARNING in a dry run."""
    if now.tzinfo is not UTC:
        raise CycleStopError(f"the clock must be UTC, got {now!r}")
    if signal_day is not None and signal_day >= now.date():
        raise CycleStopError(f"signal day {signal_day} is not before today ({now.date()})")
    if signal_day is not None and now.date() - signal_day > MAX_SIGNAL_AGE:
        raise CycleStopError(f"signal day {signal_day} is too old")
    reconciled = reconcile(broker, ledger)
    for shortfall in reconciled.shortfalls:
        log.log(
            alert, "order ended short: %s (the month end's decision stands until the next one)", shortfall
        )
    if not reconciled.clean:
        raise CycleStopError("not reconciled: " + "; ".join(reconciled.problems))

    holdings = _holdings(broker, config.universe)
    account = broker.account()
    held_quotes = {i: broker.quote(i) for i, q in holdings.items() if q > 0}
    ledger.record_snapshot(run_id, now, account.cash, _positions(holdings, held_quotes), equity_checked=False)
    _check_equity(account.cash, account.equity, holdings, held_quotes)
    ledger.mark_snapshot_checked(run_id)
    mark = ledger.high_water_mark()
    if mark is None:
        raise CycleStopError("no high-water mark: the owner must seed it (setup step)")
    if account.equity.amount > mark.amount * (1 + MAX_NEW_PEAK):
        raise CycleStopError(f"implausible new peak: {account.equity} against a mark of {mark}")
    day_start = ledger.previous_day_equity(now.date())
    last_recorded = ledger.previous_day_equity(now.date(), max_age_days=3650)  # alerts: no age limit
    ledger.update_high_water_mark(account.equity, now)
    ledger.record_equity(now.date(), account.equity)
    mark = ledger.high_water_mark()
    _alert_drawdown(last_recorded, account.equity, mark, alert)
    if signal_day is None or ledger.signal_acted(signal_day):
        return None

    bars = _checked_history(history, config.universe, signal_day, now.date())
    quotes = {i: _quote(broker, i, bars[i]) for i in config.universe}
    _cross_check(bars, quotes)
    _verify_signal_closes(ledger, bars, signal_day)
    in_trend = signals(bars, signal_day)
    before = sleeve_weights(in_trend)
    weights = vol_scaled(before, {i: daily_returns(b, signal_day) for i, b in bars.items()})
    ledger.record_signals(
        run_id, signal_day, _signal_records(run_id, bars, signal_day, in_trend, before, weights)
    )
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
            account_veto = any(veto in decision.reason for veto in ACCOUNT_VETOES)
            log.log(
                alert if account_veto else logging.WARNING,
                "%s %s: %s",
                decision.order.side,
                decision.order.instrument.ticker,
                decision.reason,
                extra={"alert": "account_veto"} if account_veto and alert >= logging.ERROR else None,
            )
        elif decision.reason.startswith("shrunk"):
            log.warning("%s %s: %s", decision.order.side, decision.order.instrument.ticker, decision.reason)
    ledger.record_decisions(
        run_id,
        [
            DecisionRecord(
                d.order.instrument,
                d.order.side,
                d.order.quantity,
                None if d.approved is None else d.approved.order.quantity,
                d.reason,
                quotes.get(d.order.instrument),
            )
            for d in decisions
        ],
    )
    return decisions


def _alert_drawdown(last: Money | None, equity: Money, mark: Money | None, alert: int) -> None:
    """A WARNING per level crossed since the last recorded equity; tagged only in a real run
    (`alert` is ERROR there), so a dry run reports it and pages nobody."""
    if mark is None:
        return
    previous = last.amount if last is not None else None
    for level in drawdown_crossings(previous, equity.amount, mark=mark.amount):
        log.warning(
            "drawdown crossed %d%% below the high-water mark",
            level,
            extra={"alert": "drawdown"} if alert >= logging.ERROR else None,
        )


def _holdings(broker: BrokerReader, universe: Mapping[Instrument, Decimal]) -> dict[Instrument, Decimal]:
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


def _quote(broker: BrokerReader, instrument: Instrument, series: Sequence[Bar]) -> Money:
    """The broker's live price; for a fund it doesn't price (Trading 212 prices held positions
    only), the data source's latest close, which then cross-checks against itself. The order
    still goes out as a market order: the price only sizes it."""
    try:
        return broker.quote(instrument)
    except NoQuoteError:
        log.info("no broker price for %s (not held): sizing from the latest close", instrument.ticker)
        return price_money(instrument, series[-1].close)


def _cross_check(bars: Mapping[Instrument, Sequence[Bar]], quotes: Mapping[Instrument, Money]) -> None:
    """The data source's latest close must be within QUOTE_TOLERANCE of the broker's live
    price, so a wrong third-party price can't flip a signal. Only held funds have a broker
    price on Trading 212 (`_quote`): for the others the close is compared with itself, so
    a month end acted on from all cash is checked against nothing (Phase 2 Decisions)."""
    for instrument, series in bars.items():
        close = price_money(instrument, series[-1].close)
        quote = quotes[instrument]
        if quote.currency is not close.currency or abs(quote.amount / close.amount - 1) > QUOTE_TOLERANCE:
            raise CycleStopError(
                f"price cross-check failed for {instrument.ticker}: data {close}, broker {quote}"
            )


def _verify_signal_closes(ledger: Ledger, bars: Mapping[Instrument, Sequence[Bar]], signal_day: date) -> None:
    """On the first run after the month end the latest close is the signal-day close, and it
    has just passed the cross-check (for held funds; see `_cross_check`): store it. A later
    run may only act on the same closes (within CLOSE_RESCALE_TOLERANCE). If they were never
    verified, the month end is skipped: one alert, then later runs only reconcile and mark
    until the next month end."""
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


def _signal_records(  # noqa: PLR0913, PLR0917 - one signal day's inputs and outputs
    run_id: str,
    bars: Mapping[Instrument, Sequence[Bar]],
    signal_day: date,
    in_trend: Mapping[Instrument, bool],
    before: Mapping[Instrument, float],
    after: Mapping[Instrument, float],
) -> list[SignalRecord]:
    held = [i for i, w in before.items() if w > 0]
    scale = after[held[0]] / before[held[0]] if held else 1.0
    rows = []
    for instrument, series in bars.items():
        closes = [b.close for b in series if b.day <= signal_day][-SMA_DAYS:]
        rows.append(
            SignalRecord(
                run_id,
                instrument,
                closes[-1],
                sum(closes) / len(closes),
                in_trend[instrument],
                before[instrument],
                after[instrument],
                scale,
            )
        )
    return rows


def _positions(
    holdings: Mapping[Instrument, Decimal], quotes: Mapping[Instrument, Money]
) -> list[PositionRecord]:
    """The snapshot's rows. A value needs a usable quote; an empty holding is worth £0."""
    rows = []
    for instrument, quantity in holdings.items():
        raw = quotes.get(instrument)
        quote = raw if raw is not None and raw.currency is instrument.currency and raw.amount > 0 else None
        if quantity == 0:
            value: Money | None = Money(Decimal(0), Currency.GBP)
        elif quote is not None:
            value = Money(quantity * quote.to_gbp().amount, Currency.GBP)
        else:
            value = None  # an unusable broker quote is not recorded as if it were a price
        rows.append(PositionRecord(instrument, quantity, quote, value))
    return rows
