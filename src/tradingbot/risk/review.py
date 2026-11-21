"""The risk veto: every proposed order is passed, shrunk or rejected (ADR-0009, ADR-0016).

Pure: the caller supplies the account state. Sells are reviewed first and are never
blocked by drawdown or loss limits (selling only lowers risk; closing everything is an
owner action). Buys fail closed: a missing high-water mark, quote, quantity step or GBP
price rejects them. Sale proceeds don't fund buys in the same run (they settle later).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, CurrencyMismatchError, Money
from tradingbot.risk.limits import Limits

ZERO = Decimal(0)
HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AccountState:
    equity: Money  # GBP, total account value now
    cash: Money  # GBP available to spend now (excludes unsettled sale proceeds)
    high_water_mark: Money | None  # GBP, from the ledger; None means unknown
    day_start_equity: Money  # GBP, at the start of the trading day
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
        positive = [
            self.equity,
            self.day_start_equity,
            *([self.high_water_mark] if self.high_water_mark else []),
        ]
        if any(a.amount <= 0 for a in positive) or self.cash.amount < 0:
            raise ValueError("impossible account state: non-positive equity or mark, or negative cash")
        if any(q < 0 for q in self.holdings.values()) or any(s <= 0 for s in self.quantity_steps.values()):
            raise ValueError("impossible account state: negative holding or non-positive quantity step")


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
    held: dict[Instrument, Decimal] = field(default_factory=dict)

    def holding(self, instrument: Instrument) -> Decimal:
        return self.held.get(instrument, self.state.holdings.get(instrument, ZERO))


def review(orders: Sequence[Order], state: AccountState, limits: Limits | None = None) -> list[Decision]:
    """Decide every order. Sells first, then buys, each in the order given."""
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
    held = run.holding(order.instrument)
    quantity = min(order.quantity, held)
    if quantity <= 0:
        return _reject(order, "nothing to sell")
    run.held[order.instrument] = held - quantity
    return _decide(order, quantity, "to the holding")


def _buy_veto(run: _Run) -> str | None:
    """A reason no buy may pass right now, from the account as a whole."""
    state, limits = run.state, run.limits
    if run.drawdown is None:
        return "no high-water mark"
    if run.drawdown >= limits.drawdown_stop:
        return "drawdown stop"
    day_loss = 1 - state.equity.amount / state.day_start_equity.amount
    if day_loss >= limits.daily_loss:
        return "daily loss limit"
    return None


def _price_and_step(run: _Run, instrument: Instrument) -> tuple[Decimal, Decimal] | str:
    """The GBP price and quantity step, or why there are none."""
    quote = run.state.quotes.get(instrument)
    step = run.state.quantity_steps.get(instrument)
    if quote is None:
        return "no quote"
    if step is None:
        return "no quantity step"
    try:
        return quote.to_gbp().amount, step
    except CurrencyMismatchError:
        return "no GBP price"


def _buy(run: _Run, order: Order) -> Decision:
    veto = _buy_veto(run)
    if veto:
        return _reject(order, veto)
    priced = _price_and_step(run, order.instrument)
    if isinstance(priced, str):
        return _reject(order, priced)
    price, step = priced

    cap = run.limits.position_fraction
    if run.drawdown is not None and run.drawdown >= run.limits.drawdown_halve:
        cap *= HALF
    held = run.holding(order.instrument)
    room_units = (cap * run.state.equity.amount - held * price) / price
    cash_units = run.cash_left / price
    limit = min(room_units, cash_units)
    quantity = _floor(min(order.quantity, limit), step)
    binding = "position cap" if room_units <= cash_units else "no cash"
    if quantity <= 0:
        return _reject(order, "below the quantity step" if order.quantity <= limit else binding)
    run.cash_left -= quantity * price
    run.held[order.instrument] = held + quantity
    if order.quantity <= limit:
        return _decide(order, quantity, "to the quantity step")
    return _decide(
        order, quantity, "to the position cap" if binding == "position cap" else "to available cash"
    )


def _floor(quantity: Decimal, step: Decimal) -> Decimal:
    if quantity <= 0:
        return ZERO
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step
