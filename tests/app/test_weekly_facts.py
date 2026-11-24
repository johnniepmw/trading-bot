"""The weekly view's figures from a real ledger holding made-up figures (ADR-0014)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.app.weekly_facts import weekly_input
from tradingbot.domain.market import BENCHMARK, Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import (
    DecisionRecord,
    IntentState,
    Ledger,
    LevelRecord,
    PositionRecord,
    PreviewRecord,
    SignalRecord,
)
from tradingbot.risk.limits import Limits

A = Instrument("AAA", Currency.GBP)
B = Instrument("BBB", Currency.GBX)
UNIVERSE = {A: Decimal("0.001"), B: Decimal("0.001")}
DAY1, DAY2, DAY3 = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)
MONTH_END = date(2026, 11, 30)
ANCHOR = {A: 100.0, B: 5000.0, BENCHMARK: 50.0}  # closes on the first levels day, as stored


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


def at(day: date, hour: int = 7) -> datetime:
    return datetime.combine(day, datetime.min.time(), UTC).replace(hour=hour, minute=15)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.create(tmp_path / "ledger.sqlite")
    opened.seed_high_water_mark(gbp("1000"), at(DAY1) - timedelta(hours=1))
    yield opened
    opened.close()


def run(  # noqa: PLR0913 - one run's facts
    ledger: Ledger,
    day: date,
    equity: str,
    closes: dict[Instrument, float] | None,
    *,
    version: str = "aaaaaaa1",
    run_id: str | None = None,
) -> str:
    rid = run_id or f"run-{day}"
    ledger.start_run(rid, at(day), version)
    ledger.record_equity(day, gbp(equity))
    if closes is not None:
        ledger.record_levels(
            rid,
            [
                LevelRecord(day - timedelta(days=1), i, c, c / 1.05 if i != BENCHMARK else None, ANCHOR[i])
                for i, c in closes.items()
            ],
        )
    ledger.finish_run(rid, at(day) + timedelta(minutes=2), "completed", None)
    return rid


def facts(ledger: Ledger, today: date = DAY3):  # type: ignore[no-untyped-def]
    return weekly_input(ledger, today=today, universe=UNIVERSE, limits=Limits(), next_month_end=MONTH_END)


def test_before_setup_there_is_no_weekly_view(tmp_path: Path) -> None:
    empty = Ledger.create(tmp_path / "empty.sqlite")
    assert facts(empty) is None
    empty.close()


def test_before_the_first_run_there_is_no_weekly_view(ledger: Ledger) -> None:
    assert facts(ledger) is None


def test_the_bot_and_both_benchmarks_start_from_the_seed_and_follow_the_closes(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", {A: 100.0, B: 5000.0, BENCHMARK: 50.0})
    run(ledger, DAY2, "1010", {A: 110.0, B: 5000.0, BENCHMARK: 55.0})
    w = facts(ledger)
    assert w is not None
    assert [(v.day, v.bot) for v in w.values] == [(DAY1, Decimal("1000")), (DAY2, Decimal("1010"))]
    assert w.values[0].equal_weight == Decimal("1000")
    assert w.values[1].equal_weight == Decimal("1050")  # half the seed up 10%
    assert w.values[1].benchmark == Decimal("1100")
    assert (w.seed, w.started, w.today) == (Decimal("1000"), DAY1, DAY3)


def test_benchmarks_that_start_later_start_from_the_bots_value_that_day(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None)  # a run from before closes were recorded
    run(ledger, DAY2, "1020", {A: 100.0, B: 5000.0, BENCHMARK: 50.0})
    run(ledger, DAY3, "1030", {A: 100.0, B: 5500.0, BENCHMARK: 50.0})
    w = facts(ledger)
    assert w is not None
    assert [v.equal_weight for v in w.values] == [None, Decimal("1020"), Decimal("1071")]
    assert [v.benchmark for v in w.values] == [None, Decimal("1020"), Decimal("1020")]


def test_a_day_without_its_benchmark_close_leaves_a_gap(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", {A: 100.0, B: 5000.0, BENCHMARK: 50.0})
    run(ledger, DAY2, "1000", {A: 100.0, B: 5000.0})
    w = facts(ledger)
    assert w is not None
    assert [v.benchmark for v in w.values] == [Decimal("1000"), None]


def test_a_day_without_equity_is_left_out(ledger: Ledger) -> None:
    ledger.start_run("stopped", at(DAY2), "v")  # stopped before the equity check
    ledger.finish_run("stopped", at(DAY2), "stopped", "broker down")
    run(ledger, DAY3, "1000", None)
    w = facts(ledger)
    assert w is not None
    assert [v.day for v in w.values] == [DAY3]


def test_funds_combine_the_latest_preview_levels_and_snapshot(ledger: Ledger) -> None:
    rid = run(ledger, DAY1, "1000", {A: 105.0, B: 5000.0})
    ledger.record_preview(rid, [PreviewRecord(DAY1, A, True, 0.5), PreviewRecord(DAY1, B, False, 0.0)])
    ledger.record_snapshot(
        rid,
        at(DAY1),
        gbp("750"),
        [
            PositionRecord(A, Decimal("2.5"), Money(Decimal("100"), Currency.GBP), gbp("250")),
            PositionRecord(B, Decimal("0"), None, gbp("0")),
        ],
        equity_checked=True,
    )
    w = facts(ledger)
    assert w is not None
    fund_a, fund_b = w.funds
    assert (fund_a.ticker, fund_a.in_trend, fund_a.held, fund_a.weight_now, fund_a.weight_preview) == (
        "AAA",
        True,
        Decimal("250"),
        0.25,
        0.5,
    )
    assert fund_a.distance == pytest.approx(0.05)
    assert (fund_b.in_trend, fund_b.held, fund_b.weight_now) == (False, Decimal("0"), 0.0)
    assert w.preview_as_of == DAY1 - timedelta(days=1)


def test_funds_without_any_records_show_unknowns(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None)
    w = facts(ledger)
    assert w is not None
    assert [(f.in_trend, f.distance, f.weight_preview) for f in w.funds] == [(None, None, None)] * 2
    assert w.preview_as_of is None


def test_a_run_with_levels_but_no_snapshot_leaves_holdings_unknown(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", {A: 105.0, B: 5000.0})
    w = facts(ledger)
    assert w is not None
    assert [(f.held, f.weight_now) for f in w.funds] == [(None, None)] * 2


def test_the_last_month_ends_decisions_are_in_pounds(ledger: Ledger) -> None:
    rid = run(ledger, DAY1, "1000", None)
    ledger.record_decisions(
        rid,
        [
            DecisionRecord(A, Side.BUY, Decimal("2"), Decimal("2"), "within limits", gbp("100")),
            DecisionRecord(
                B, Side.BUY, Decimal("4"), Decimal("2"), "cap", Money(Decimal("5000"), Currency.GBX)
            ),
            DecisionRecord(A, Side.SELL, Decimal("1"), None, "no quote", None),
        ],
    )
    run(ledger, DAY2, "1000", None)  # a quiet day after: the month end's decisions still show
    w = facts(ledger)
    assert w is not None
    assert w.decisions_day == DAY1
    assert [(d.ticker, d.side, d.proposed, d.approved) for d in w.decisions] == [
        ("AAA", "buy", Decimal("200"), Decimal("200")),
        ("BBB", "buy", Decimal("200"), Decimal("100")),
        ("AAA", "sell", None, None),
    ]


def test_no_decisions_yet(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None)
    w = facts(ledger)
    assert w is not None
    assert (w.decisions_day, list(w.decisions)) == (None, [])


def test_a_new_version_between_runs_is_a_deploy(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None, version="1111111aaaa")
    run(ledger, DAY2, "1000", None, version="1111111aaaa")
    run(ledger, DAY3, "1000", None, version="2222222bbbb")
    w = facts(ledger)
    assert w is not None
    assert [(c.day, c.text) for c in w.changes] == [(DAY3, "deploy 2222222")]


def test_limits_and_month_end_come_from_the_caller(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None)
    w = facts(ledger)
    assert w is not None
    assert (w.drawdown_halve, w.drawdown_stop, w.next_month_end) == (
        Limits().drawdown_halve,
        Limits().drawdown_stop,
        MONTH_END,
    )


def test_benchmarks_are_total_return_through_the_anchor(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", {A: 100.0, B: 5000.0, BENCHMARK: 50.0})
    rid = "run-rescaled"
    ledger.start_run(rid, at(DAY2), "v")
    ledger.record_equity(DAY2, gbp("1000"))
    # a dividend: the provider rescaled A's history, so the same close now means +2% total return
    ledger.record_levels(
        rid,
        [
            LevelRecord(DAY1, A, 100.0, None, 98.0),
            LevelRecord(DAY1, B, 5000.0, None, 5000.0),
            LevelRecord(DAY1, BENCHMARK, 50.0, None, None),  # anchor out of reach: no figure that day
        ],
    )
    ledger.finish_run(rid, at(DAY2), "completed", None)
    w = facts(ledger)
    assert w is not None
    assert w.values[1].equal_weight == pytest.approx(Decimal("1010.204"), abs=Decimal("0.001"))
    assert w.values[1].benchmark is None


def test_the_mark_comes_from_the_ledger(ledger: Ledger) -> None:
    run(ledger, DAY1, "1000", None)
    ledger.update_high_water_mark(gbp("1100"), at(DAY1))
    w = facts(ledger)
    assert w is not None
    assert w.mark == Decimal("1100")


def test_month_end_trades_carry_why_the_order_state_the_fill_and_fees(ledger: Ledger) -> None:
    rid = run(ledger, DAY1, "1000", None)
    ledger.record_signals(
        rid,
        DAY1 - timedelta(days=1),
        [
            SignalRecord(rid, A, 105.0, 100.0, True, 0.0, 0.25, 1.0),
            SignalRecord(rid, B, 4900.0, 5000.0, False, 0.0, 0.0, 1.0),
        ],
    )
    ledger.record_decisions(
        rid,
        [
            DecisionRecord(A, Side.BUY, Decimal("2"), Decimal("2"), "within limits", gbp("100")),
            DecisionRecord(
                B, Side.BUY, Decimal("1"), Decimal("1"), "within limits", Money(Decimal("5000"), Currency.GBX)
            ),
        ],
    )
    filled = ledger.record_intent(rid, Order(A, Side.BUY, Decimal("2")), at(DAY1))
    ledger.settle(
        filled, IntentState.FILLED, "o-1", Decimal("2"), average_price=gbp("100.5"), fees=gbp("0.10")
    )
    waiting = ledger.record_intent(rid, Order(B, Side.BUY, Decimal("1")), at(DAY1))
    ledger.mark_sent(waiting, "o-2")
    w = facts(ledger)
    assert w is not None
    a, b = w.decisions
    assert (a.why, a.order, a.intended, a.filled_at, a.fees, a.traded, a.pence) == (
        "in trend, target 25%",
        "filled",
        Decimal("100"),
        Decimal("100.5"),
        Decimal("0.10"),
        Decimal("201.0"),
        False,
    )
    assert (b.why, b.order, b.intended, b.filled_at, b.pence) == (
        "out of trend, target 0%",
        "awaiting fill",
        Decimal("5000"),
        None,
        True,
    )


def test_a_decision_without_an_order_has_no_order_state(ledger: Ledger) -> None:
    rid = run(ledger, DAY1, "1000", None)
    ledger.record_decisions(
        rid, [DecisionRecord(A, Side.BUY, Decimal("2"), None, "drawdown stop", gbp("100"))]
    )
    w = facts(ledger)
    assert w is not None
    assert (w.decisions[0].order, w.decisions[0].why) == (None, None)
