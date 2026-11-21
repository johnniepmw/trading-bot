"""Sleeve weights, the volatility cap and orders to reach the targets (ADR-0015, ADR-0016).

Weights are floats (statistics); quantities become Decimals at one place, `orders_for`,
which rounds down to each instrument's quantity step. Pure: callers pass everything in.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import CurrencyMismatchError, Money

SLEEVE_CAP = 0.25
VOL_TARGET = 0.10
VOL_DAYS = 60
TRADING_DAYS_PER_YEAR = 252
REBALANCE_BAND_TARGET = 0.10  # trade only if off target by more than 10% of the target...
REBALANCE_BAND_EQUITY = 0.02  # ...or 2% of equity, whichever is larger


def sleeve_weights(signals: Mapping[Instrument, bool]) -> dict[Instrument, float]:
    """Each instrument is a fixed sleeve of min(1/N, 25%); a failing sleeve stays cash."""
    if not signals:
        return {}
    sleeve = min(1 / len(signals), SLEEVE_CAP)
    return {instrument: sleeve if held else 0.0 for instrument, held in signals.items()}


def vol_scaled(
    weights: Mapping[Instrument, float], returns: Mapping[Instrument, Sequence[float]]
) -> dict[Instrument, float]:
    """Scale all weights down (never up) if estimated annual volatility exceeds VOL_TARGET.

    The estimate uses the last VOL_DAYS daily returns of the weighted portfolio.
    """
    held = {i: w for i, w in weights.items() if w > 0}
    if not held:
        return dict(weights)
    for instrument in held:
        if len(returns.get(instrument, ())) < VOL_DAYS:
            raise ValueError(f"{instrument.ticker}: need {VOL_DAYS} daily returns to estimate volatility")
        if not all(math.isfinite(r) for r in returns[instrument][-VOL_DAYS:]):
            raise ValueError(f"{instrument.ticker}: daily returns must be finite")
    portfolio = [sum(w * returns[i][-VOL_DAYS:][t] for i, w in held.items()) for t in range(VOL_DAYS)]
    vol = statistics.stdev(portfolio) * math.sqrt(TRADING_DAYS_PER_YEAR)
    scale = min(1.0, VOL_TARGET / vol) if vol > 0 else 1.0
    return {i: w * scale for i, w in weights.items()}


def _floor(quantity: Decimal, step: Decimal) -> Decimal:
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _order(  # noqa: PLR0913 - one instrument's sizing inputs
    instrument: Instrument,
    *,
    weight: float,
    held: Decimal,
    price: Money | None,
    step: Decimal | None,
    equity: Decimal,
) -> Order | None:
    if price is None or step is None:
        return None
    try:
        unit = price.to_gbp().amount
    except CurrencyMismatchError:
        return None
    target = Decimal(repr(weight)) * equity
    diff = target - held * unit
    band = max(Decimal(repr(REBALANCE_BAND_TARGET)) * target, Decimal(repr(REBALANCE_BAND_EQUITY)) * equity)
    if abs(diff) <= band:
        return None
    if diff < 0:
        quantity = held if target == 0 else min(held, _floor(-diff / unit, step))
        side = Side.SELL
    else:
        quantity, side = _floor(diff / unit, step), Side.BUY
    return Order(instrument, side, quantity) if quantity > 0 else None


def orders_for(
    targets: Mapping[Instrument, float],
    *,
    holdings: Mapping[Instrument, Decimal],
    prices: Mapping[Instrument, Money],
    equity: Money,
    steps: Mapping[Instrument, Decimal],
) -> list[Order]:
    """Orders that move holdings to the target weights, outside the rebalance band.

    Instruments held but not targeted are sold. Missing prices or steps mean no order.
    Everything goes through the risk layer before it can be sent.
    """
    total = equity.to_gbp().amount
    instruments = sorted({*targets, *holdings}, key=lambda i: (i.ticker, i.currency))
    orders = (
        _order(
            i,
            weight=targets.get(i, 0.0),
            held=holdings.get(i, Decimal(0)),
            price=prices.get(i),
            step=steps.get(i),
            equity=total,
        )
        for i in instruments
    )
    return [o for o in orders if o is not None]
