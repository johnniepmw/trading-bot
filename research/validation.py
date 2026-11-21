"""Validation statistics for strategy specs (docs/strategies/trend-long-or-cash.md).

Research tooling only: `src` never imports this. Standard library only.
Sharpe ratios passed between functions are per period (daily) unless the name says
"annual". References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014);
Politis & Romano, "The Stationary Bootstrap" (1994).
"""

from __future__ import annotations

import csv
import itertools
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tradingbot.data.prices import validate_history
from tradingbot.domain.market import Bar, Instrument, price_money

TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649015329
_NORMAL = statistics.NormalDist()


def max_drawdown(values: Sequence[float]) -> float:
    """Deepest fall from a running peak, as a positive fraction."""
    peak, worst = -math.inf, 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def returns_of(values: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in itertools.pairwise(values)]


def annual_sharpe(returns: Sequence[float], risk_free_annual: float = 0.0) -> float:
    excess = [r - risk_free_annual / TRADING_DAYS for r in returns]
    if len(excess) < 2:  # noqa: PLR2004 - a standard deviation needs two points
        return 0.0
    sd = statistics.stdev(excess)
    return 0.0 if sd == 0 else statistics.fmean(excess) / sd * math.sqrt(TRADING_DAYS)


def _moments(returns: Sequence[float]) -> tuple[float, float, float]:
    """Per-period Sharpe, skewness and (non-excess) kurtosis."""
    mean, sd = statistics.fmean(returns), statistics.pstdev(returns)
    if sd == 0:
        return 0.0, 0.0, 3.0
    skew = statistics.fmean(((r - mean) / sd) ** 3 for r in returns)
    kurt = statistics.fmean(((r - mean) / sd) ** 4 for r in returns)
    return mean / statistics.stdev(returns), skew, kurt


def probabilistic_sharpe(sharpe: float, benchmark: float, *, n: int, skew: float, kurtosis: float) -> float:
    """Probability that the true per-period Sharpe exceeds `benchmark`, given n observations."""
    spread = math.sqrt(max(1e-12, 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2))
    return _NORMAL.cdf((sharpe - benchmark) * math.sqrt(n - 1) / spread)


def deflated_sharpe(returns: Sequence[float], trial_sharpes: Sequence[float]) -> float:
    """Deflated Sharpe Ratio: the probabilistic Sharpe against the best Sharpe expected from
    len(trial_sharpes) unskilled trials. `trial_sharpes` are every logged trial's per-period
    Sharpe, this one included."""
    sharpe, skew, kurt = _moments(returns)
    trials = len(trial_sharpes)
    if trials < 2:  # noqa: PLR2004 - one trial: nothing to deflate against
        expected_max = 0.0
    else:
        spread = statistics.stdev(trial_sharpes)
        expected_max = spread * (
            (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
            + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * math.e))
        )
    return probabilistic_sharpe(sharpe, expected_max, n=len(returns), skew=skew, kurtosis=kurt)


def stationary_bootstrap(
    returns: Sequence[float], *, mean_block: int, samples: int, seed: int
) -> list[list[float]]:
    """Resampled return paths that keep runs of consecutive days (mean length `mean_block`),
    so volatility clustering survives. Seeded, so results are repeatable."""
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    n, paths = len(returns), []
    for _ in range(samples):
        index, path = rng.randrange(n), list[float]()
        while len(path) < n:
            path.append(returns[index])
            index = rng.randrange(n) if rng.random() < 1 / mean_block else (index + 1) % n
        paths.append(path)
    return paths


def drawdown_quantile(
    returns: Sequence[float], *, q: float, samples: int = 5000, seed: int = 0, mean_block: int = 20
) -> float:
    """The q-quantile of maximum drawdown across bootstrapped paths."""
    depths = sorted(
        max_drawdown(list(itertools.accumulate(path, lambda v, r: v * (1 + r), initial=1.0)))
        for path in stationary_bootstrap(returns, mean_block=mean_block, samples=samples, seed=seed)
    )
    return depths[min(len(depths) - 1, math.ceil(q * len(depths)) - 1)]


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    sharpe: float
    max_drawdown: float


def rolling_windows(days: Sequence[date], equity: Sequence[float], *, years: int = 5) -> list[Window]:
    """Consecutive, non-overlapping windows of `years` years (parameters are fixed, so this is
    sub-period analysis, not walk-forward refitting)."""
    windows, start = list[Window](), 0
    while True:
        first = days[start]
        target = first.replace(year=first.year + years)
        end = next((j for j in range(start, len(days)) if days[j] >= target), None)
        if end is None:
            return windows
        part = equity[start : end + 1]
        windows.append(Window(first, days[end], annual_sharpe(returns_of(part)), max_drawdown(part)))
        start = end


def buy_and_hold(history: Mapping[Instrument, Sequence[Bar]], initial: float) -> list[float]:
    """The 1/N buy-and-hold benchmark, bought at the closes of the first day all assets trade."""
    closes = {
        i: {b.day: float(price_money(i, b.close).to_gbp().amount) for b in bars}
        for i, bars in history.items()
    }
    common = sorted(set.intersection(*(set(c) for c in closes.values())))
    shares = {i: initial / len(closes) / c[common[0]] for i, c in closes.items()}
    return [sum(shares[i] * c[d] for i, c in closes.items()) for d in common]


def log_trial(path: Path, name: str, params: Mapping[str, object], *, sharpe: float, days: int) -> None:
    """Append one trial to the trial log (its count feeds the Deflated Sharpe Ratio)."""
    if not path.exists():
        path.write_text("| trial | params | per-day sharpe | days |\n|---|---|---|---|\n")
    with path.open("a") as log:
        log.write(f"| {name} | {dict(params)} | {sharpe:.6f} | {days} |\n")


def load_bars_csv(path: Path, instrument: Instrument) -> list[Bar]:
    """Read `date,open,close` rows (adjusted prices, quote currency) and validate them."""
    with path.open(newline="") as file:
        bars = [
            Bar(
                instrument,
                date.fromisoformat(row["date"]),
                open=float(row["open"]),
                close=float(row["close"]),
            )
            for row in csv.DictReader(file)
        ]
    return list(validate_history(instrument, bars))
