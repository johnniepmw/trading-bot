"""The glance's seven lines and their colours, from made-up figures (ADR-0014)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tradingbot.domain.money import Currency, Money
from tradingbot.report.glance import GlanceInput, Level, glance, render


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


AS_OF = datetime(2026, 11, 24, 7, 16, tzinfo=UTC)
GOOD = GlanceInput(
    as_of=AS_OF,
    outcome="completed",
    reason=None,
    equity=gbp("1010"),
    day_start=gbp("1000"),
    mark=gbp("1010"),
    seed=gbp("1000"),
    cash=gbp("410"),
    invested=gbp("600"),
    funds_held=3,
    funds_total=7,
    snapshot_checked=True,
    sent_today=0,
    awaiting_fill=0,
    open_orders=0,
    next_month_end=date(2026, 11, 30),
    in_trend=4,
    daily_loss_limit=Decimal("0.02"),
    drawdown_halve=Decimal("0.10"),
    drawdown_stop=Decimal("0.20"),
)


def levels(g: GlanceInput) -> list[Level]:
    return [line.level for line in glance(g)]


def line(g: GlanceInput, n: int) -> tuple[Level, str]:
    found = glance(g)[n]
    return found.level, found.text


def test_a_quiet_good_day_is_seven_ok_lines() -> None:
    assert levels(GOOD) == [Level.OK] * 7


def test_the_lines_read_in_pounds_and_percent() -> None:
    texts = [x.text for x in glance(GOOD)]
    assert texts == [
        "Run: completed Tue 24 Nov 07:16 UTC",
        "Today: £1,010.00 (+£10.00, +1.0%)",
        "Drawdown: 0.0% (£0.00) below the peak of £1,010.00",
        "Since setup: +£10.00 (+1.0%) on £1,000.00",
        "Holdings: 59% invested in 3 of 7 funds; cash £410.00",
        "Orders: 0 sent today (0 awaiting fill); 0 open from earlier",
        "Next month end: Mon 30 Nov; 4 of 7 funds in trend today (3 held)",
    ]


@pytest.mark.parametrize(
    ("outcome", "reason", "level", "text"),
    [
        ("stopped", "not reconciled", Level.STOP, "Run: STOPPED Tue 24 Nov 07:16 UTC: not reconciled"),
        ("stopped", None, Level.STOP, "Run: STOPPED Tue 24 Nov 07:16 UTC: no reason given"),
        ("running", None, Level.WARN, "Run: in progress since Tue 24 Nov 07:16 UTC"),
        (
            "died",
            "x",
            Level.STOP,
            "Run: did not finish (started Tue 24 Nov 07:16 UTC); the next run reports it",
        ),
    ],
)
def test_the_run_line(outcome: str, reason: str | None, level: Level, text: str) -> None:
    assert line(replace(GOOD, outcome=outcome, reason=reason), 0) == (level, text)


@pytest.mark.parametrize(
    ("equity", "level"),
    [("990", Level.WARN), ("980.01", Level.WARN), ("980", Level.STOP), ("950", Level.STOP)],
)
def test_a_loss_today_warns_and_stops_at_the_daily_loss_limit(equity: str, level: Level) -> None:
    assert line(replace(GOOD, equity=gbp(equity), mark=gbp("1100")), 1)[0] is level


def test_no_day_start_is_a_stop_line() -> None:
    assert line(replace(GOOD, day_start=None), 1) == (
        Level.STOP,
        "Today: £1,010.00; no day-start equity (buys paused)",
    )


def test_no_equity_is_a_stop_on_every_line_that_needs_it() -> None:
    g = replace(GOOD, equity=None)
    assert [levels(g)[i] for i in (1, 2, 3)] == [Level.STOP, Level.STOP, Level.WARN]


@pytest.mark.parametrize(
    ("equity", "level", "suffix"),
    [
        ("960", Level.OK, "below the peak of £1,000.00"),
        ("950", Level.WARN, "below the peak of £1,000.00"),
        ("900", Level.STOP, "; positions halved"),
        ("800", Level.STOP, "; buy stop"),
    ],
)
def test_drawdown_levels(equity: str, level: Level, suffix: str) -> None:
    got_level, text = line(replace(GOOD, equity=gbp(equity), mark=gbp("1000")), 2)
    assert got_level is level and text.endswith(suffix)


def test_drawdown_names_the_gap_in_pounds() -> None:
    assert line(replace(GOOD, equity=gbp("900"), mark=gbp("1000")), 2)[1].startswith(
        "Drawdown: 10.0% (£100.00)"
    )


def test_a_loss_since_setup_is_amber_never_red() -> None:
    assert line(replace(GOOD, equity=gbp("700")), 3) == (
        Level.WARN,
        "Since setup: -£300.00 (-30.0%) on £1,000.00",
    )


def test_an_unverified_snapshot_is_a_stop() -> None:
    assert line(replace(GOOD, snapshot_checked=False), 4)[0] is Level.STOP


def test_no_snapshot_is_amber() -> None:
    assert line(replace(GOOD, snapshot_checked=None), 4) == (Level.WARN, "Holdings: no snapshot this run")


def test_all_cash_is_zero_percent_invested() -> None:
    g = replace(GOOD, cash=gbp("0"), invested=gbp("0"))
    assert "0% invested" in line(g, 4)[1]


def test_orders_from_earlier_runs_still_open_are_a_stop() -> None:
    assert line(replace(GOOD, open_orders=1, sent_today=2), 5) == (
        Level.STOP,
        "Orders: 2 sent today (0 awaiting fill); 1 open from earlier",
    )


def test_this_runs_orders_awaiting_the_open_are_normal() -> None:
    # a 07:15 run sends before the 08:00 open: its orders fill later
    assert line(replace(GOOD, sent_today=3, awaiting_fill=3), 5)[0] is Level.OK


def test_no_preview_is_amber() -> None:
    assert line(replace(GOOD, in_trend=None), 6)[0] is Level.WARN


def test_missing_mark_or_seed() -> None:
    assert levels(replace(GOOD, mark=None, seed=None))[2:4] == [Level.STOP, Level.WARN]


@pytest.mark.parametrize(
    ("g", "label"),
    [(GOOD, "all OK"), (replace(GOOD, in_trend=None), "check"), (replace(GOOD, open_orders=1), "ACTION")],
)
def test_the_subject_says_the_worst_line(g: GlanceInput, label: str) -> None:
    subject, _ = render(glance(g), as_of=AS_OF, environment="prod")
    assert subject == f"Trading bot (prod) Tue 24 Nov: {label}"


def test_the_body_marks_each_line_and_has_no_links() -> None:
    _, body = render(glance(replace(GOOD, open_orders=1)), as_of=AS_OF, environment="prod")
    assert body.splitlines()[0] == "[OK]   Run: completed Tue 24 Nov 07:16 UTC"
    assert "[STOP] Orders: 0 sent today (0 awaiting fill); 1 open from earlier" in body
    assert "http" not in body
    assert body.endswith(
        "As of 2026-11-24 07:16 UTC. Figures from the bot's ledger; details in Cloud Logging."
    )
