from __future__ import annotations

import itertools
import math
import random
from datetime import date
from pathlib import Path

import pytest
from research.validation import (
    annual_sharpe,
    buy_and_hold,
    deflated_sharpe,
    drawdown_quantile,
    load_bars_csv,
    log_trial,
    max_drawdown,
    probabilistic_sharpe,
    rolling_windows,
    stationary_bootstrap,
)

from tests.bars import bars
from tradingbot.data.prices import DataError
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency

EQ = Instrument("EQ", Currency.GBP)


def test_max_drawdown_is_the_deepest_fall_from_a_peak() -> None:
    assert max_drawdown([100, 120, 90, 130, 117]) == pytest.approx(0.25)
    assert max_drawdown([100, 101, 102]) == 0.0


def test_annual_sharpe_of_a_known_series() -> None:
    returns = [0.001, -0.0005] * 126
    mean, sd = 0.00025, math.sqrt(sum((r - 0.00025) ** 2 for r in returns) / 251)
    assert annual_sharpe(returns) == pytest.approx(mean / sd * math.sqrt(252))
    assert annual_sharpe(returns, risk_free_annual=0.0252) < annual_sharpe(returns)


def test_a_flat_series_has_zero_sharpe() -> None:
    assert annual_sharpe([0.0] * 10) == 0.0


def test_probabilistic_sharpe_is_one_half_at_the_benchmark() -> None:
    assert probabilistic_sharpe(0.05, 0.05, n=500, skew=0.0, kurtosis=3.0) == pytest.approx(0.5)
    assert probabilistic_sharpe(0.10, 0.0, n=500, skew=0.0, kurtosis=3.0) > 0.95


def test_more_trials_deflate_the_sharpe() -> None:
    rng = random.Random(1)  # noqa: S311 - synthetic returns
    returns = [0.0006 + rng.gauss(0, 0.01) for _ in range(2500)]
    one = deflated_sharpe(returns, trial_sharpes=[0.06])
    many = deflated_sharpe(returns, trial_sharpes=[rng.gauss(0.03, 0.02) for _ in range(50)])
    assert many < one


def test_stationary_bootstrap_is_seeded_and_keeps_length() -> None:
    returns = [float(i) for i in range(100)]
    a = stationary_bootstrap(returns, mean_block=20, samples=3, seed=5)
    assert a == stationary_bootstrap(returns, mean_block=20, samples=3, seed=5)
    assert [len(s) for s in a] == [100, 100, 100]
    # blocks keep runs of consecutive days (volatility clustering survives)
    runs = sum(1 for s in a for x, y in itertools.pairwise(s) if y == (x + 1) % 100)
    assert runs > 200


def test_drawdown_quantile_of_a_steady_series_is_small() -> None:
    assert drawdown_quantile([0.001] * 300, q=0.95, samples=200, seed=1) == 0.0


def test_rolling_windows_cover_the_history_in_five_year_steps() -> None:
    days = [date(2000 + y, 1, 3) for y in range(12)]
    equity = [100.0 * 1.05**y for y in range(12)]
    windows = rolling_windows(days, equity, years=5)
    assert [(w.start.year, w.end.year) for w in windows] == [(2000, 2005), (2005, 2010)]
    assert all(w.max_drawdown == 0.0 for w in windows)


def test_buy_and_hold_splits_equally_on_the_first_common_day() -> None:
    a, b = Instrument("A", Currency.GBP), Instrument("B", Currency.GBP)
    curve = buy_and_hold({a: bars(a, [100.0, 110.0]), b: bars(b, [50.0, 45.0])}, initial=1000.0)
    assert curve == pytest.approx([1000.0, 500 * 1.1 + 500 * 0.9])


def test_trials_are_appended_to_the_log(tmp_path: Path) -> None:
    log = tmp_path / "trial-log.md"
    log_trial(log, "base", {"sma_days": 210}, sharpe=0.61, days=5000)
    log_trial(log, "sma150", {"sma_days": 150}, sharpe=0.55, days=5000)
    lines = log.read_text().splitlines()
    assert lines[0].startswith("| trial |")
    assert len([line for line in lines if line.startswith("| base") or line.startswith("| sma150")]) == 2


def test_csv_bars_are_loaded_and_validated(tmp_path: Path) -> None:
    good = tmp_path / "eq.csv"
    good.write_text("date,open,close\n2020-01-02,100,101\n2020-01-03,101,102\n")
    assert [b.close for b in load_bars_csv(good, EQ)] == [101.0, 102.0]
    bad = tmp_path / "bad.csv"
    bad.write_text("date,open,close\n2020-01-02,100,101\n2020-01-03,10100,10200\n")
    with pytest.raises(DataError):
        load_bars_csv(bad, EQ)
