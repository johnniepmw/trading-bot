"""Hypothesis properties: no approved order breaks position, cash or loss limits."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.risk.limits import Limits
from tradingbot.risk.review import AccountState, review

INSTRUMENTS = [
    Instrument("AAA", Currency.GBP),
    Instrument("BBB", Currency.GBX),
    Instrument("CCC", Currency.GBP),
]
STEPS = {i: s for i, s in zip(INSTRUMENTS, [Decimal("0.01"), Decimal("0.001"), Decimal(1)], strict=True)}


def money(lo: str, hi: str) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=Decimal(lo), max_value=Decimal(hi), places=2, allow_nan=False, allow_infinity=False
    )


orders = st.lists(
    st.builds(
        Order,
        st.sampled_from(INSTRUMENTS),
        st.sampled_from(list(Side)),
        st.decimals(min_value=Decimal("0.001"), max_value=Decimal(500), places=3),
    ),
    max_size=8,
)


@st.composite
def states(draw: st.DrawFn) -> AccountState:
    equity = draw(money("1", "5000"))
    gbp = lambda x: Money(x, Currency.GBP)  # noqa: E731
    return AccountState(
        equity=gbp(equity),
        cash=gbp(draw(money("0", "5000"))),
        high_water_mark=draw(st.none() | money("1", "8000").map(gbp)),
        day_start_equity=gbp(draw(money("1", "5000"))),
        holdings={i: draw(st.decimals(min_value=0, max_value=100, places=3)) for i in INSTRUMENTS},
        quotes={
            i: Money(draw(money("0.01", "5000")), i.currency) for i in INSTRUMENTS if draw(st.booleans())
        },
        quantity_steps=STEPS,
    )


limits_strategy = st.builds(
    Limits,
    position_fraction=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.30"), places=2),
    drawdown_halve=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.10"), places=2),
    drawdown_stop=st.decimals(min_value=Decimal("0.11"), max_value=Decimal("0.20"), places=2),
    daily_loss=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.05"), places=2),
)


@settings(max_examples=400)
@given(orders, states(), limits_strategy)
def test_no_approved_order_breaks_a_limit(proposed: list[Order], state: AccountState, limits: Limits) -> None:
    decisions = review(proposed, state, limits)
    assert sorted(map(id, (d.order for d in decisions))) == sorted(map(id, proposed))  # one decision each

    spent = Decimal(0)
    held = {i: state.holdings.get(i, Decimal(0)) for i in INSTRUMENTS}
    mark = state.high_water_mark
    drawdown = None if mark is None else max(Decimal(0), 1 - state.equity.amount / mark.amount)
    day_loss = 1 - state.equity.amount / state.day_start_equity.amount
    seen_buy = False
    for d in decisions:
        if d.order.side is Side.BUY:
            seen_buy = True
        else:
            assert not seen_buy  # sells are decided first
        if d.approved is None:
            continue
        o = d.approved.order
        assert o.instrument == d.order.instrument and o.side is d.order.side
        assert 0 < o.quantity <= d.order.quantity  # never grows
        if o.side is Side.SELL:
            assert o.quantity <= held[o.instrument]  # no shorting
            held[o.instrument] -= o.quantity
            continue
        # buys: every gate held
        assert drawdown is not None and drawdown < limits.drawdown_stop
        assert day_loss < limits.daily_loss
        assert o.quantity % STEPS[o.instrument] == 0
        price = state.quotes[o.instrument].to_gbp().amount
        spent += o.quantity * price
        held[o.instrument] += o.quantity
        cap = limits.position_fraction * (Decimal("0.5") if drawdown >= limits.drawdown_halve else 1)
        assert held[o.instrument] * price <= cap * state.equity.amount  # position cap
    assert spent <= state.cash.amount  # no leverage


@settings(max_examples=200)
@given(orders, states(), limits_strategy)
def test_a_sell_of_something_held_is_always_approved(
    proposed: list[Order], state: AccountState, limits: Limits
) -> None:
    left = dict(state.holdings)
    for d in review(proposed, state, limits):
        if d.order.side is not Side.SELL:
            continue
        step = STEPS[d.order.instrument]
        sellable = min(d.order.quantity, left[d.order.instrument])
        if left[d.order.instrument] > 0 and (sellable == left[d.order.instrument] or sellable >= step):
            assert d.approved is not None, d.reason  # liveness: sells are never blocked
            left[d.order.instrument] -= d.approved.order.quantity
