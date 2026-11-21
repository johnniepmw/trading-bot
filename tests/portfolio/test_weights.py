from __future__ import annotations

import math
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.portfolio.weights import (
    REBALANCE_BAND_EQUITY,
    REBALANCE_BAND_TARGET,
    SLEEVE_CAP,
    VOL_DAYS,
    VOL_TARGET,
    orders_for,
    sleeve_weights,
    vol_scaled,
)

UNIVERSE = [Instrument(t, Currency.GBP) for t in ("A", "B", "C", "D", "E", "F", "G")]
A, B, C = UNIVERSE[:3]


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def test_the_spec_constants() -> None:
    assert (SLEEVE_CAP, VOL_TARGET, VOL_DAYS) == (0.25, 0.10, 60)
    assert (REBALANCE_BAND_TARGET, REBALANCE_BAND_EQUITY) == (0.10, 0.02)


def test_each_sleeve_is_one_nth_and_failing_sleeves_stay_cash() -> None:
    signals = {i: i in (A, B) for i in UNIVERSE}
    weights = sleeve_weights(signals)
    assert weights[A] == pytest.approx(1 / 7)
    assert weights[C] == 0.0
    assert sum(weights.values()) == pytest.approx(2 / 7)  # not redistributed


def test_sleeves_are_capped_at_25_percent() -> None:
    weights = sleeve_weights({A: True, B: True})
    assert weights == {A: 0.25, B: 0.25}


def test_no_signals_means_no_weights() -> None:
    assert sleeve_weights({}) == {}


@given(st.dictionaries(st.sampled_from(UNIVERSE), st.booleans()))
def test_weights_are_never_negative_over_the_cap_or_over_100_percent(signals: dict[Instrument, bool]) -> None:
    weights = sleeve_weights(signals)
    assert all(0.0 <= w <= SLEEVE_CAP for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-12


def flat_then(values: list[float]) -> list[float]:
    return [0.0] * (VOL_DAYS - len(values)) + values


def test_calm_markets_leave_weights_alone() -> None:
    returns = {A: [0.001, -0.001] * 30}  # about 1.6% a year
    assert vol_scaled({A: 0.25}, returns) == {A: 0.25}


def test_volatility_above_target_scales_every_weight_down() -> None:
    returns = {A: [0.02, -0.02] * 30, B: [0.02, -0.02] * 30}  # perfectly correlated, ~32% a year each
    scaled = vol_scaled({A: 0.25, B: 0.25}, returns)
    portfolio_vol = 0.5 * 0.02 * math.sqrt(252) * math.sqrt(60 / 59)
    assert scaled[A] == pytest.approx(0.25 * VOL_TARGET / portfolio_vol)
    assert scaled[A] == scaled[B]


def test_only_the_last_60_returns_count() -> None:
    returns = {A: [0.5] * 100 + [0.001, -0.001] * 30}
    assert vol_scaled({A: 0.25}, returns) == {A: 0.25}


def test_cash_only_portfolios_are_not_scaled() -> None:
    assert vol_scaled({A: 0.0}, {}) == {A: 0.0}


def test_a_held_asset_without_60_returns_is_an_error() -> None:
    with pytest.raises(ValueError, match="60"):
        vol_scaled({A: 0.25}, {A: [0.01] * 59})


@given(st.lists(st.floats(-0.2, 0.2), min_size=60, max_size=60), st.floats(0.0, 0.25))
def test_scaling_never_increases_exposure(returns: list[float], weight: float) -> None:
    scaled = vol_scaled({A: weight}, {A: returns})
    assert 0.0 <= scaled[A] <= weight


# --- orders ------------------------------------------------------------------------
STEPS = {A: Decimal("0.01"), B: Decimal("0.01")}


def test_buys_up_to_the_target_value() -> None:
    orders = orders_for(
        {A: 0.25}, holdings={A: Decimal(0)}, prices={A: gbp(100)}, equity=gbp(1000), steps=STEPS
    )
    assert orders == [Order(A, Side.BUY, Decimal("2.50"))]


def test_sells_down_to_the_target_value() -> None:
    orders = orders_for(
        {A: 0.0}, holdings={A: Decimal(3)}, prices={A: gbp(100)}, equity=gbp(1000), steps=STEPS
    )
    assert orders == [Order(A, Side.SELL, Decimal(3))]


def test_small_differences_inside_the_band_are_not_traded() -> None:
    # target £250, holding £240: £10 is under max(10% of £250, 2% of £1000) = £25
    orders = orders_for(
        {A: 0.25}, holdings={A: Decimal("2.4")}, prices={A: gbp(100)}, equity=gbp(1000), steps=STEPS
    )
    assert orders == []


def test_held_instruments_missing_from_the_targets_are_sold() -> None:
    orders = orders_for({}, holdings={B: Decimal(1)}, prices={B: gbp(100)}, equity=gbp(1000), steps=STEPS)
    assert orders == [Order(B, Side.SELL, Decimal(1))]


def test_pence_prices_are_converted_to_pounds() -> None:
    gbx = Instrument("G", Currency.GBX)
    orders = orders_for(
        {gbx: 0.25},
        holdings={gbx: Decimal(0)},
        prices={gbx: Money(Decimal(4000), Currency.GBX)},
        equity=gbp(1000),
        steps={gbx: Decimal("0.01")},
    )
    assert orders == [Order(gbx, Side.BUY, Decimal("6.25"))]


def test_quantities_round_down_to_the_step_and_tiny_orders_are_dropped() -> None:
    orders = orders_for(
        {A: 0.25}, holdings={A: Decimal(0)}, prices={A: gbp(30)}, equity=gbp(1000), steps=STEPS
    )
    assert orders == [Order(A, Side.BUY, Decimal("8.33"))]
    none = orders_for(
        {A: 0.25}, holdings={A: Decimal(0)}, prices={A: gbp(30)}, equity=gbp(1000), steps={A: Decimal(10)}
    )
    assert none == []


def test_an_instrument_without_a_price_or_step_gets_no_order() -> None:
    # Missing data never invents a trade; risk would reject it anyway.
    assert orders_for({A: 0.25}, holdings={A: Decimal(0)}, prices={}, equity=gbp(1000), steps=STEPS) == []
    assert (
        orders_for({A: 0.25}, holdings={A: Decimal(0)}, prices={A: gbp(100)}, equity=gbp(1000), steps={})
        == []
    )


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_non_finite_return_is_an_error_not_a_skipped_cap(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        vol_scaled({A: 0.25}, {A: [0.01] * 59 + [bad]})
