"""Day-by-day backtest of the long-or-cash trend system (ADR-0015, ADR-0016).

It shares `strategy`, `portfolio` and `risk` with the live job and has its own fill
simulator (it can't use `execution` or brokers). Each trading day:
1. at the open, fill the orders risk approved on the previous signal date, at that open
   plus costs (sells first; a buy that the open makes unaffordable is shrunk to cash);
2. at the close, mark equity and the high-water mark;
3. on a completed month's last trading day, compute signals, sleeve weights, the
   volatility cap and orders, and pass them through `risk.review` with this day's state.
Sale proceeds never fund buys decided in the same run, as in live trading.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from tradingbot.data.prices import validate_history
from tradingbot.domain.market import Bar, Instrument, Order, Side, price_money
from tradingbot.domain.money import Currency, Money
from tradingbot.portfolio.weights import VOL_TARGET, daily_returns, orders_for, sleeve_weights, vol_scaled
from tradingbot.risk.limits import Limits
from tradingbot.risk.review import AccountState, Decision, review
from tradingbot.strategy.trend import SMA_DAYS, month_end_days, signals

_CONTEXT = Context(prec=28, rounding=ROUND_FLOOR, traps=[InvalidOperation, DivisionByZero, Overflow])
ZERO = Decimal(0)


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: Decimal  # GBP
    steps: Mapping[Instrument, Decimal]  # quantity step per instrument
    cost_per_side: Decimal = Decimal("0.0015")  # spread and FX, as a fraction of traded value
    limits: Limits = field(default_factory=Limits)
    sma_days: int = SMA_DAYS  # production value; others only for logged stability trials
    vol_target: float = VOL_TARGET


@dataclass(frozen=True, slots=True)
class Fill:
    day: date  # the fill (open) date
    signal_day: date  # the month end that decided it
    instrument: Instrument
    side: Side
    quantity: Decimal
    price_gbp: Decimal
    cost: Decimal  # GBP
    holding_after: Decimal


@dataclass(frozen=True)
class Result:
    equity: list[tuple[date, Decimal]]  # GBP at each close
    cash: list[Decimal]  # GBP at each close
    fills: list[Fill]
    decisions: list[tuple[date, Decision]]


def _gbp(bar_price: float, instrument: Instrument) -> Decimal:
    return price_money(instrument, bar_price).to_gbp().amount


@dataclass
class _Book:
    cash: Decimal
    holdings: dict[Instrument, Decimal]
    last_close: dict[Instrument, Decimal] = field(default_factory=dict)

    def equity(self) -> Decimal:
        return self.cash + sum((q * self.last_close[i] for i, q in self.holdings.items() if q), ZERO)


def run(history: Mapping[Instrument, Sequence[Bar]], config: BacktestConfig) -> Result:
    """Simulate the strategy over `history` (adjusted daily bars per instrument)."""
    for instrument, bars in history.items():
        validate_history(instrument, bars)
    with localcontext(_CONTEXT):
        return _run(history, config)


def _run(history: Mapping[Instrument, Sequence[Bar]], config: BacktestConfig) -> Result:
    by_day = {i: {b.day: b for b in bars} for i, bars in history.items()}
    days = sorted({d for bars in by_day.values() for d in bars})
    signal_days = month_end_days(days)
    book = _Book(config.initial_cash, {i: ZERO for i in history})
    mark = config.initial_cash
    day_start = config.initial_cash
    pending: list[tuple[date, Order]] = []
    result = Result([], [], [], [])
    for day in days:
        _fill(book, pending=pending, by_day=by_day, day=day, config=config, result=result)
        pending = []
        for i, bars in by_day.items():
            if day in bars:
                book.last_close[i] = _gbp(bars[day].close, i)
        equity = book.equity()
        mark = max(mark, equity)
        result.equity.append((day, equity))
        result.cash.append(book.cash)
        if day in signal_days:
            pending = _decide(
                book,
                history=history,
                by_day=by_day,
                day=day,
                equity=equity,
                mark=mark,
                day_start=day_start,
                config=config,
                result=result,
            )
        day_start = equity
    return result


def _decide(  # noqa: PLR0913 - one day's full state
    book: _Book,
    *,
    history: Mapping[Instrument, Sequence[Bar]],
    by_day: Mapping[Instrument, Mapping[date, Bar]],
    day: date,
    equity: Decimal,
    mark: Decimal,
    day_start: Decimal,
    config: BacktestConfig,
    result: Result,
) -> list[tuple[date, Order]]:
    weights = sleeve_weights(signals(history, day, config.sma_days))
    returns = {i: daily_returns(bars, day) for i, bars in history.items()}
    weights = vol_scaled(weights, returns, config.vol_target)
    quotes = {i: price_money(i, bars[day].close) for i, bars in by_day.items() if day in bars}
    gbp = Currency.GBP
    orders = orders_for(
        weights, holdings=book.holdings, prices=quotes, equity=Money(equity, gbp), steps=config.steps
    )
    state = AccountState(
        equity=Money(equity, gbp),
        cash=Money(book.cash, gbp),
        high_water_mark=Money(mark, gbp),
        day_start_equity=Money(day_start, gbp),
        holdings=book.holdings,
        quotes=quotes,
        quantity_steps=config.steps,
    )
    decisions = review(orders, state, config.limits)
    result.decisions.extend((day, d) for d in decisions)
    return [(day, d.approved.order) for d in decisions if d.approved is not None]


def _fill(  # noqa: PLR0913 - the day's fills need the book, the orders and the open prices
    book: _Book,
    *,
    pending: Sequence[tuple[date, Order]],
    by_day: Mapping[Instrument, Mapping[date, Bar]],
    day: date,
    config: BacktestConfig,
    result: Result,
) -> None:
    for signal_day, order in pending:  # risk already ordered sells before buys
        bar = by_day[order.instrument].get(day)
        if bar is None:
            continue  # no session for this fund today: the order lapses
        price = _gbp(bar.open, order.instrument)
        quantity = order.quantity
        if order.side is Side.BUY:
            # the open may gap above the signal close: never spend more than the cash
            affordable = book.cash / (price * (1 + config.cost_per_side))
            step = config.steps[order.instrument]
            quantity = min(quantity, (affordable / step).to_integral_value() * step)
            if quantity <= 0:
                continue
        value = quantity * price
        cost = value * config.cost_per_side
        sign = 1 if order.side is Side.BUY else -1
        book.cash -= sign * value + cost
        book.holdings[order.instrument] += sign * quantity
        result.fills.append(
            Fill(
                day,
                signal_day,
                order.instrument,
                order.side,
                quantity,
                price,
                cost,
                book.holdings[order.instrument],
            )
        )
