"""The risk veto: every proposed order is passed, shrunk or rejected (ADR-0009, ADR-0016).

Pure: the caller supplies the account state. Sells are reviewed first and are never
blocked by drawdown or loss limits (selling only lowers risk; closing everything is an
owner action). Buys fail closed: a missing high-water mark, quote, quantity step or GBP
price rejects them. Sale proceeds don't fund buys in the same run (they settle later).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from types import MappingProxyType

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, CurrencyMismatchError, Money
from tradingbot.risk.limits import HARD_STOP_DRAWDOWN, MAX_DAILY_LOSS, MAX_POSITION_FRACTION, Limits

ZERO = Decimal(0)
# Fixed arithmetic: round down, and always raise on invalid, divide-by-zero and overflow,
# whatever the caller's own Decimal context says.
_CONTEXT = Context(prec=28, rounding=ROUND_FLOOR, traps=[InvalidOperation, DivisionByZero, Overflow])
HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AccountState:
    equity: Money  # GBP, total account value now
    cash: Money  # GBP available to spend now (excludes unsettled sale proceeds)
    high_water_mark: Money | None  # GBP, from the ledger; None means unknown
    day_start_equity: Money | None  # GBP, at the start of the trading day; None means unknown
    # Broker positions mapped to instruments, with an explicit zero for every instrument the
    # strategy may buy. A broker position that maps to no instrument is the caller's error
    # and must stop the run before review (a ledger/execution failure drill, AC9): filling
    # in zeros for unmapped positions would hide a real holding from the position cap.
    holdings: Mapping[Instrument, Decimal]
    quotes: Mapping[Instrument, Money]  # latest price in the instrument's quote currency
    quantity_steps: Mapping[Instrument, Decimal]  # smallest tradable fraction per instrument

    def __post_init__(self) -> None:
        amounts = {
            "equity": self.equity,
            "cash": self.cash,
            "high_water_mark": self.high_water_mark,
            "day_start_equity": self.day_start_equity,
        }
        for name, amount in amounts.items():
            if amount is not None and amount.currency is not Currency.GBP:
                raise CurrencyMismatchError(f"{name} must be in GBP, got {amount.currency}")
        positive = [a for a in (self.equity, self.day_start_equity, self.high_water_mark) if a is not None]
        if any(a.amount <= 0 for a in positive) or self.cash.amount < 0:
            raise ValueError("impossible account state: non-positive equity or mark, or negative cash")
        for name in ("holdings", "quotes", "quantity_steps"):
            # a private, read-only copy: later changes to the caller's dict can't skip these checks
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        # Holdings decide sells too, so a bad holding stops the whole run. A bad quote or step
        # only rejects buys of that instrument (see _price_and_step).
        if len({i.ticker for i in self.holdings}) != len(self.holdings):
            raise ValueError("impossible account state: a ticker appears under two currencies in holdings")
        for value in self.holdings.values():
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"impossible account state: holding must be a finite Decimal, got {value!r}")
            if value < 0:
                raise ValueError(f"impossible account state: negative holding {value}")


@dataclass(frozen=True, slots=True)
class Decision:
    order: Order  # as proposed
    approved: ApprovedOrder | None
    reason: str  # "passed", "shrunk: ..." or "rejected: ..."


@dataclass
class _Run:
    state: AccountState
    limits: Limits
    cash_left: Decimal
    drawdown: Decimal | None  # None when the high-water mark is unknown
    held: dict[Instrument, Decimal] = field(default_factory=dict)  # after this run's sells
    bought: dict[Instrument, Decimal] = field(default_factory=dict)  # by this run's buys

    def holding(self, instrument: Instrument) -> Decimal:
        return self.held.get(instrument, self.state.holdings.get(instrument, ZERO))


def review(orders: Sequence[Order], state: AccountState, limits: Limits | None = None) -> list[Decision]:
    """Decide every order. Sells first, then buys, each in the order given.

    Arithmetic runs in a fixed context that rounds down, so a caller's Decimal settings
    can never round a quantity up past a limit.
    """
    with localcontext(_CONTEXT):
        return _review(orders, state, limits)


def _review(orders: Sequence[Order], state: AccountState, limits: Limits | None) -> list[Decision]:
    mark = state.high_water_mark
    drawdown = None if mark is None else max(ZERO, 1 - state.equity.amount / mark.amount)
    run = _Run(state, limits or Limits(), state.cash.amount, drawdown)
    sells = [o for o in orders if o.side is Side.SELL]
    buys = [o for o in orders if o.side is Side.BUY]
    return [_sell(run, o) for o in sells] + [_buy(run, o) for o in buys]


def _decide(order: Order, quantity: Decimal, why_less: str) -> Decision:
    if quantity == order.quantity:
        return Decision(order, ApprovedOrder(order), "passed")
    approved = ApprovedOrder(Order(order.instrument, order.side, quantity))
    return Decision(order, approved, f"shrunk: {why_less}")


def _reject(order: Order, why: str) -> Decision:
    return Decision(order, None, f"rejected: {why}")


def _sell(run: _Run, order: Order) -> Decision:
    """Sells are never blocked by limits. A full exit is exact; a partial one rounds down to the step."""
    held = run.holding(order.instrument)
    if held <= 0:
        return _reject(order, "nothing to sell")
    if order.quantity >= held:
        quantity, why = held, "to the holding"
    else:
        step = _valid_step(run.state.quantity_steps.get(order.instrument))
        quantity = order.quantity if step is None else _floor(order.quantity, step)
        why = "to the quantity step"
        if quantity <= 0:
            return _reject(order, "below the quantity step")
    run.held[order.instrument] = held - quantity
    return _decide(order, quantity, why)


def _buy_veto(run: _Run) -> str | None:
    """A reason no buy may pass right now, from the account as a whole."""
    state, limits = run.state, run.limits
    if run.drawdown is None:
        return "no high-water mark"
    if state.day_start_equity is None:
        return "no day-start equity"
    # ceilings are re-applied here, so a tampered Limits object still can't loosen them
    if run.drawdown >= min(limits.drawdown_stop, HARD_STOP_DRAWDOWN):
        return "drawdown stop"
    day_loss = 1 - state.equity.amount / state.day_start_equity.amount
    if day_loss >= min(limits.daily_loss, MAX_DAILY_LOSS):
        return "daily loss limit"
    return None


def _valid_step(step: object) -> Decimal | None:
    if isinstance(step, Decimal) and step.is_finite() and step > 0:
        return step
    return None


def _ambiguous(instrument: Instrument, mapping: Mapping[Instrument, object]) -> bool:
    return sum(1 for i in mapping if i.ticker == instrument.ticker) > 1


def _quote_problem(instrument: Instrument, quote: Money | None) -> str | None:
    if quote is None:
        return "no quote"
    if quote.currency is not instrument.currency:
        return "quote currency does not match instrument"
    if quote.amount <= 0:
        return "bad quote"
    return None


def _price_and_step(run: _Run, instrument: Instrument) -> tuple[Decimal, Decimal] | str:
    """The GBP price and quantity step, or why there are none."""
    state = run.state
    if _ambiguous(instrument, state.quotes) or _ambiguous(instrument, state.quantity_steps):
        return "ambiguous quote"
    quote = state.quotes.get(instrument)
    raw_step = state.quantity_steps.get(instrument)
    step = _valid_step(raw_step)
    problem = _quote_problem(instrument, quote) or (
        "no quantity step" if raw_step is None else "bad quantity step" if step is None else None
    )
    if problem or quote is None or step is None:
        return problem or "no quote"
    try:
        return quote.to_gbp().amount, step
    except CurrencyMismatchError:
        return "no GBP price"


def _buy(run: _Run, order: Order) -> Decision:
    veto = _buy_veto(run)
    if veto:
        return _reject(order, veto)
    if order.instrument not in run.state.holdings:
        return _reject(order, "no holding record")
    priced = _price_and_step(run, order.instrument)
    if isinstance(priced, str):
        return _reject(order, priced)
    price, step = priced

    cap = min(run.limits.position_fraction, MAX_POSITION_FRACTION)
    if run.drawdown is not None and run.drawdown >= run.limits.drawdown_halve:
        cap *= HALF
    # Cap room counts the holding before this run's sells: a sell may not fill, and a buy
    # must not rely on it (the same reason sale proceeds don't fund buys).
    held = run.state.holdings[order.instrument] + run.bought.get(order.instrument, ZERO)
    room_units = (cap * run.state.equity.amount - held * price) / price
    cash_units = run.cash_left / price
    limit = min(room_units, cash_units)
    quantity = _floor(min(order.quantity, limit), step)
    binding = "position cap" if room_units <= cash_units else "no cash"
    if quantity <= 0:
        return _reject(order, "below the quantity step" if order.quantity <= limit else binding)
    run.cash_left -= quantity * price
    run.bought[order.instrument] = run.bought.get(order.instrument, ZERO) + quantity
    if order.quantity <= limit:
        return _decide(order, quantity, "to the quantity step")
    return _decide(
        order, quantity, "to the position cap" if binding == "position cap" else "to available cash"
    )


def _floor(quantity: Decimal, step: Decimal) -> Decimal:
    if quantity <= 0:
        return ZERO
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step
