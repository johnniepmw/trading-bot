from __future__ import annotations

import json
import math
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.backtest.synthetic import UNIVERSE, market
from tests.bars import bars, weekdays
from tradingbot.backtest.engine import BacktestConfig, run
from tradingbot.data.prices import DataError
from tradingbot.domain.market import Bar, Instrument, Side
from tradingbot.domain.money import Currency
from tradingbot.strategy.trend import month_end_days

STEPS = {i: Decimal("0.0001") for i in UNIVERSE}
CONFIG = BacktestConfig(initial_cash=Decimal(1000), steps=STEPS)
REGRESSION = Path(__file__).with_name("regression.json")


@pytest.fixture(scope="module")
def history() -> dict[Instrument, list]:
    return market()


@pytest.fixture(scope="module")
def result(history):
    return run(history, CONFIG)


def test_cash_only_until_any_trend_is_established(result) -> None:
    # 210 days are needed before any signal; the first fills come after that
    first_fill = min(f.day for f in result.fills)
    assert first_fill > sorted({b.day for b in market()[UNIVERSE[0]]})[209]


def test_trades_happen_only_the_session_after_a_month_end(history, result) -> None:
    days = sorted({b.day for bars_ in history.values() for b in bars_})
    after_month_end = {days[i + 1] for i, d in enumerate(days[:-1]) if d in month_end_days(days)}
    assert {f.day for f in result.fills} <= after_month_end  # weights fixed between signal dates


def test_fills_are_at_the_next_open_with_costs(history, result) -> None:
    fill = result.fills[0]
    bar = next(b for b in history[fill.instrument] if b.day == fill.day)
    expected_price = Decimal(repr(bar.open)) / (100 if fill.instrument.currency is Currency.GBX else 1)
    assert fill.price_gbp == expected_price
    assert fill.cost == fill.quantity * fill.price_gbp * CONFIG.cost_per_side


def test_equity_is_marked_every_day_and_never_negative(history, result) -> None:
    days = sorted({b.day for bars_ in history.values() for b in bars_})
    assert [d for d, _ in result.equity] == days
    assert all(e > 0 for _, e in result.equity)
    assert result.equity[0][1] == Decimal(1000)


def test_cash_never_goes_negative(result) -> None:
    assert min(result.cash) >= 0


def test_no_holding_is_bought_above_the_risk_cap(history, result) -> None:
    # every buy passed risk.review; spot-check the post-buy value against 25% of the day's equity
    equity = dict(result.equity)
    for f in (f for f in result.fills if f.side is Side.BUY):
        assert f.holding_after * f.price_gbp <= Decimal("0.25") * equity[f.signal_day] * Decimal(
            "1.10"
        )  # open gap


def test_a_truncated_history_gives_the_same_past(history) -> None:
    # no lookahead: cutting the future off never changes what already happened
    full = run(history, CONFIG)
    cut_day = full.equity[500][0]
    cut = run({i: [b for b in bs if b.day <= cut_day] for i, bs in history.items()}, CONFIG)
    assert cut.equity == full.equity[:501]
    assert cut.fills == [f for f in full.fills if f.day <= cut_day]


def test_double_costs_cost_more(history, result) -> None:
    doubled = run(
        history, BacktestConfig(initial_cash=Decimal(1000), steps=STEPS, cost_per_side=Decimal("0.003"))
    )
    assert doubled.equity[-1][1] < result.equity[-1][1]


def test_neighbour_parameters_change_the_result(history, result) -> None:
    other = run(
        history, BacktestConfig(initial_cash=Decimal(1000), steps=STEPS, sma_days=150, vol_target=0.05)
    )
    assert other.equity[-1][1] != result.equity[-1][1]


def test_bad_history_is_refused() -> None:
    eq = UNIVERSE[0]
    broken = bars(eq, [100.0, 10_000.0])  # pounds read as pence
    with pytest.raises(DataError):
        run({eq: broken}, CONFIG)


def test_an_unadjusted_ten_for_one_split_is_refused() -> None:
    # history must be adjusted for corporate actions; a raw split looks like a 10x jump
    eq = UNIVERSE[0]
    with pytest.raises(DataError):
        run({eq: bars(eq, [100.0, 10.0])}, CONFIG)


def test_a_fund_that_starts_later_stays_cash_until_it_has_210_days() -> None:
    # survivorship-safe: a late listing is not back-filled, then trades once it has history
    eq, late = UNIVERSE[0], UNIVERSE[2]
    days = weekdays(date(2018, 1, 1), 600)
    history = {
        eq: bars(eq, [100 + t * 0.1 for t in range(600)]),
        late: bars(late, [50 + t * 0.1 for t in range(300)], start=days[300]),
    }
    late_fills = [f.day for f in run(history, CONFIG).fills if f.instrument == late]
    late_days = sorted(b.day for b in history[late])
    assert late_fills  # it does trade once it has 210 days
    assert min(late_fills) > late_days[209]


def test_regression_on_the_frozen_synthetic_market(result) -> None:
    # Changing these numbers needs a stated reason in the PR (docs/testing.md, plan step 5).
    expected = json.loads(REGRESSION.read_text())
    actual = {
        "final_equity": str(result.equity[-1][1].quantize(Decimal("0.01"))),
        "fills": len(result.fills),
        "first_fill": str(result.fills[0].day),
    }
    assert actual == expected, f"regression changed: {actual}"


# --- review round 1 ----------------------------------------------------------------
def _first_buy_fraction(res) -> float:
    equity = dict(res.equity)
    buys = [f for f in res.fills if f.side is Side.BUY]
    first = buys[0].signal_day
    return max(float(f.quantity * f.price_gbp / equity[first]) for f in buys if f.signal_day == first)


def test_the_volatility_cap_binds_when_the_target_is_low(history, result) -> None:
    capped = run(history, BacktestConfig(initial_cash=Decimal(1000), steps=STEPS, vol_target=0.02))
    assert _first_buy_fraction(result) > 0.2  # uncapped: a full 25% sleeve
    assert _first_buy_fraction(capped) < 0.15  # a ~4%-vol sleeve scaled to a 2% target
    assert capped.equity[-1][1] != result.equity[-1][1]


def test_buys_never_spend_sale_proceeds_from_the_same_day(result) -> None:
    # each day's buys (with costs) fit in the cash at the previous close, before that day's sells
    days = [d for d, _ in result.equity]
    for k, day in enumerate(days[1:], start=1):
        spent = sum(
            (f.quantity * f.price_gbp + f.cost for f in result.fills if f.day == day and f.side is Side.BUY),
            Decimal(0),
        )
        assert spent <= result.cash[k - 1]


def test_a_later_price_never_changes_a_signal_days_decisions(history, result) -> None:
    # perturb every bar after a signal day: that day's decisions must not change (no one-day lookahead)
    signal_day = sorted({d for d, _ in result.decisions})[2]
    shocked = {
        i: [b if b.day <= signal_day else Bar(i, b.day, open=b.open * 1.5, close=b.close * 1.5) for b in bs]
        for i, bs in history.items()
    }
    again = run(shocked, CONFIG)
    assert [d for d in again.decisions if d[0] == signal_day] == [
        d for d in result.decisions if d[0] == signal_day
    ]


def test_an_order_for_a_fund_without_a_session_waits_for_its_next_one() -> None:
    eq, other = UNIVERSE[0], UNIVERSE[2]
    days = weekdays(date(2018, 1, 1), 260)
    up = [100 + t * 0.2 for t in range(260)]
    signal_day = min(d for d in month_end_days(days) if d >= days[209])  # the first buy
    gap = days[days.index(signal_day) + 1]  # eq has no session the day after the signal
    history = {
        eq: [Bar(eq, d, open=c, close=c) for d, c in zip(days, up, strict=True) if d != gap],
        other: [Bar(other, d, open=50.0, close=50.0) for d in days],
    }
    fills = [f for f in run(history, CONFIG).fills if f.instrument == eq and f.signal_day == signal_day]
    assert [f.day for f in fills] == [days[days.index(gap) + 1]]


@pytest.mark.parametrize(
    "bad",
    [{"sma_days": 1}, {"sma_days": -5}, {"vol_target": 0.0}, {"vol_target": 15.0}, {"vol_target": math.nan}],
)
def test_backtest_parameters_are_checked(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"sma_days|vol_target"):
        BacktestConfig(initial_cash=Decimal(1000), steps=STEPS, **bad)  # type: ignore[arg-type]
