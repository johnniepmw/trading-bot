"""The weekly view from made-up figures (ADR-0014)."""
# ruff: noqa: RUF001 - the page prints true minus signs (weekly.MINUS), so the tests do too

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

from tradingbot.report import weekly
from tradingbot.report.weekly import (
    Change,
    DayValue,
    DecisionRow,
    FundRow,
    WeeklyInput,
    buy_and_hold,
    drawdowns,
    weekly_html,
)

DAYS = [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)]
VALUES = [
    DayValue(DAYS[0], Decimal("1000"), Decimal("1000"), Decimal("1000")),
    DayValue(DAYS[1], Decimal("1010"), Decimal("1005"), Decimal("1012")),
    DayValue(DAYS[2], Decimal("990"), Decimal("1001"), Decimal("1003")),
    DayValue(DAYS[3], Decimal("1020"), Decimal("1008"), Decimal("1030")),
    DayValue(DAYS[4], Decimal("1015"), Decimal("1009"), Decimal("1025")),
]
FUNDS = [
    FundRow("VWRP", True, 0.046, Decimal("238"), 0.236, 0.22),
    FundRow("EMIM", False, -0.018, Decimal("0"), 0.0, 0.0),
    FundRow("SGLN", True, 0.25, Decimal("120"), 0.12, 0.11),
]
DECISIONS = [
    DecisionRow("VWRP", "buy", Decimal("240"), Decimal("240"), "within limits"),
    DecisionRow("VUKE", "buy", Decimal("180"), Decimal("120"), "positions halved <drawdown>"),
    DecisionRow("SGLN", "buy", Decimal("160"), None, "no new buys", refused=True),
]
W = WeeklyInput(
    values=VALUES,
    seed=Decimal("1000"),
    started=DAYS[0],
    today=DAYS[4],
    funds=FUNDS,
    preview_as_of=DAYS[3],
    next_month_end=date(2026, 11, 30),
    decisions_day=date(2026, 10, 30),
    decisions=DECISIONS,
    changes=[Change(DAYS[2], "deploy <abc1234>")],
    drawdown_halve=Decimal("0.10"),
    drawdown_stop=Decimal("0.20"),
)


def html(w: WeeklyInput = W) -> str:
    hero, cards = weekly_html(w)
    return hero + cards


def test_buy_and_hold_splits_the_seed_equally_and_holds() -> None:
    start = {"A": Decimal("10"), "B": Decimal("20")}
    assert buy_and_hold(Decimal("1000"), start, {"A": Decimal("11"), "B": Decimal("20")}) == Decimal("1050")
    assert buy_and_hold(Decimal("1000"), start, {"A": Decimal("11")}) is None
    assert buy_and_hold(Decimal("1000"), {}, {}) is None


def test_drawdown_is_measured_from_the_running_high_including_the_seed() -> None:
    dd = drawdowns(W)
    assert dd[0] == 0.0
    assert round(dd[2], 4) == round(990 / 1010 - 1, 4)
    assert dd[3] == 0.0
    assert round(dd[4], 4) == round(1015 / 1020 - 1, 4)


def test_the_hero_gives_value_changes_and_the_gap_to_ssac() -> None:
    h = html()
    assert "<p class=hero>£1,015.00</p>" in h
    assert "−£5.00 (−0.49%)</span> <span class=muted>today" in h
    assert "+£15.00 (+1.50%)</span> <span class=muted>since setup" in h
    assert "1.0% behind SSAC" in h
    ahead = replace(W, values=[*VALUES[:-1], DayValue(DAYS[4], Decimal("1040"), None, Decimal("1025"))])
    assert "1.5% ahead of SSAC" in html(ahead)


def test_the_value_chart_has_three_series_cash_a_legend_and_hover_titles() -> None:
    h = html()
    for cls in ("s-bot", "s-ew", "s-bm"):
        assert f'<polyline class="line {cls}"' in h
    assert "<line class=cash" in h
    assert "Bot <b>£1,015</b> +1.5%" in h
    assert "SSAC <b>£1,025</b> +2.5%" in h
    assert "Cash <b>£1,000</b> 0.0%" in h
    assert "<title>Fri 27 Nov: bot £1,015.00 · 1/N £1,009.00 · SSAC £1,025.00 · cash £1,000.00</title>" in h


def test_deploys_are_marked_and_escaped() -> None:
    h = html()
    assert "<title>Wed 25 Nov: deploy &lt;abc1234&gt;</title>" in h
    assert "25 Nov deploy &lt;abc1234&gt;" in h
    assert "<abc1234>" not in h


def test_a_missing_benchmark_drops_its_line_but_keeps_the_page() -> None:
    no_bm = replace(W, values=[replace(v, benchmark=None) for v in VALUES])
    h = html(no_bm)
    assert 'class="line s-bm"' not in h
    assert "SSAC <b>—</b>" in h
    assert "behind SSAC" not in h


def test_one_day_shows_a_note_not_a_chart() -> None:
    h = html(replace(W, values=VALUES[:1]))
    assert "The chart starts after the second run." in h
    assert '<polyline class="line s-bot"' in h  # the drawdown line still draws, flat


def test_no_runs_yet_draws_no_lines_and_no_hero() -> None:
    hero, cards = weekly_html(replace(W, values=[]))
    assert hero == ""
    assert "<polyline" not in cards


def test_the_drawdown_card_draws_both_limits_with_words() -> None:
    h = html()
    assert "positions halve" in h and "no new buys" in h
    assert 'class="limit serious"' in h and 'class="limit critical"' in h
    assert "<b>−0.5%</b> <span class=muted>now</span>" in h
    assert "<b>−2.0%</b> <span class=muted>worst</span>" in h
    assert "<b>9.5%</b> <span class=muted>room before halving</span>" in h


def test_funds_show_trend_by_icon_distance_held_and_preview() -> None:
    h = html()
    assert "<span class='trend in' aria-label='in trend'>▲</span><b>VWRP</b>" in h
    assert "<span class='trend out' aria-label='out of trend'>▽</span><b>EMIM</b>" in h
    assert "+4.6%" in h and "−1.8%" in h
    assert "£238<span class=small>24%</span>" in h
    assert "<b>2 of 3</b> in trend on Thu 26 Nov's closes" in h


def test_a_distance_beyond_the_scale_is_clamped_but_shown_in_full() -> None:
    h = html()
    assert "+25.0%" in h
    widths = [float(x) for x in re.findall(r'class="bar in" x="48.0" y="2" width="([0-9.]+)"', h)]
    assert max(widths) <= 48


def test_funds_without_a_preview_say_so() -> None:
    bare = replace(W, funds=[FundRow("VWRP", None, None, Decimal("0"), 0.0, None)], preview_as_of=None)
    h = html(bare)
    assert "aria-label='no preview'" in h
    assert "If today were the month end" not in h


def test_month_end_shows_now_and_preview_splits_and_decisions() -> None:
    h = html()
    assert "Next month end <b>Mon 30 Nov</b>" in h and "(in 3 days)" in h
    assert "<b>36%</b> invested" in h  # now: 23.6% + 12%
    assert "If today were the month end" in h and "<b>33%</b> invested" in h
    assert "<span class='status good' aria-hidden=true>✓</span> approved" in h
    assert "<span class='status warning' aria-hidden=true>!</span> cut to £120" in h
    assert "<span class='status critical' aria-hidden=true>✕</span> refused" in h
    assert "positions halved &lt;drawdown&gt;" in h


def test_before_the_first_month_end_there_are_no_decisions() -> None:
    assert "No month end acted on yet" in html(replace(W, decisions_day=None, decisions=[]))
    assert "Fri 30 Oct: nothing to trade." in html(replace(W, decisions=[]))


def test_paper_trading_counts_days_from_the_first_run_up_to_90() -> None:
    assert "<b>Day 5</b> of 90" in html()
    assert "ends Sat 20 Feb 2027" in html()
    assert "<b>Day 90</b> of 90" in html(replace(W, today=date(2027, 6, 1)))


def test_the_numbers_table_lists_every_day_newest_first() -> None:
    h = html()
    table = h[h.index("<table class=nums>") :]
    assert table.index("Fri 27 Nov") < table.index("Mon 23 Nov")
    assert "<td>Wed 25 Nov</td><td>£990</td><td>£1,001</td><td>£1,003</td><td>−2.0%</td>" in table


def test_no_scripts_and_no_colour_in_text() -> None:
    h = html()
    assert "<script" not in h and "style=" not in h
    assert weekly.MINUS == "−"


def test_series_within_a_pound_still_draw_with_pence_labels() -> None:
    seed = Decimal("1000.37")
    flat = [
        DayValue(DAYS[0], seed, seed, seed),
        DayValue(DAYS[1], Decimal("1000.41"), Decimal("1000.40"), Decimal("1000.39")),
    ]
    h = html(replace(W, values=flat, seed=seed))
    assert 'class="line s-bot"' in h
    assert re.search(r"text-anchor=end>£1,000\.[0-9]{2}</text>", h)


def test_benchmarks_that_start_after_setup_say_since_when() -> None:
    late = [
        DayValue(DAYS[0], Decimal("1000"), None, None),
        DayValue(DAYS[1], Decimal("1010"), Decimal("1010"), Decimal("1010")),
        DayValue(DAYS[2], Decimal("1020"), Decimal("1030"), Decimal("1040")),
    ]
    h = html(replace(W, values=late))
    assert "2.0% behind SSAC (the MSCI ACWI tracker) since 24 Nov" in h  # +1.0% against +3.0%
    assert "1/N and SSAC start 24 Nov at the bot's value" in h
    assert "SSAC <b>£1,040</b> +3.0%" in h  # from its own start


def test_benchmarks_from_setup_say_since_setup() -> None:
    h = html()
    assert "behind SSAC (the MSCI ACWI tracker) since setup" in h
    assert "start" not in h[h.index("<ul class=legend>") : h.index("<svg", h.index("<ul class=legend>"))]


def test_drawdown_now_and_room_use_the_ledgers_mark() -> None:
    h = html(replace(W, mark=Decimal("1100")))
    assert "<b>−7.7%</b> <span class=muted>now</span>" in h  # 1015 / 1100
    assert "<b>2.3%</b> <span class=muted>room before halving</span>" in h
    past = html(replace(W, mark=Decimal("1300")))
    assert "<b>0.0%</b> <span class=muted>room before halving</span>" in past


def test_unknown_holdings_never_read_as_cash() -> None:
    unknown = replace(W, funds=[replace(f, held=None, weight_now=None) for f in FUNDS])
    h = html(unknown)
    assert "Now: unknown" in h
    assert "invested · " not in h[: h.index("If today were")]


def test_a_decision_without_a_quote_shows_no_amount() -> None:
    rows = [DecisionRow("VWRP", "sell", None, None, "no quote")]
    h = html(replace(W, decisions=rows))
    assert "<td>sell —</td>" in h
    assert "✓</span> approved" in h


def test_the_halving_limit_says_what_halves() -> None:
    assert "position cap halves" in html()
