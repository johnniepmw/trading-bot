"""The weekly view (dashboard slice 2; docs/exec-plans/active/2026-11-23-dashboard.md).

Pure: SVG and HTML built from figures the app reads from the ledger. No scripts (the CSP
forbids them), so hover is each chart's `<title>`; every chart also has its numbers in a
table, so nothing depends on colour or on hover alone.

Colour by job (dataviz palette, validated light and dark): the three series are the
categorical slots in fixed order (bot blue, 1/N orange, SSAC aqua); cash is a muted dashed
reference; in/out of trend is blue against neutral grey; the drawdown limits wear the
reserved status colours, always with a text label. Text is always ink, never a series colour.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from html import escape

FUND_NAMES = {
    "VWRP": "Global equity",
    "VUKE": "UK equity",
    "EMIM": "Emerging markets",
    "IGLT": "UK gilts",
    "VAGP": "Global bonds",
    "INXG": "Index-linked gilts",
    "SGLN": "Gold",
}
PAPER_DAYS = 90
MINUS = "\u2212"  # a true minus sign, as wide as "+"
MIDDLE_LABEL_FROM = 5  # days on a chart before its middle date gets a label
DISTANCE_SCALE = 0.10  # the funds' distance bars run from -10% to +10% (clamped, value shown)


@dataclass(frozen=True, slots=True)
class DayValue:
    day: date
    bot: Decimal  # equity at the day's run, GBP
    equal_weight: Decimal | None  # the seed split equally over the 7 funds at setup, held
    benchmark: Decimal | None  # the seed in SSAC at setup, held


@dataclass(frozen=True, slots=True)
class FundRow:
    ticker: str
    in_trend: bool | None  # on the latest preview; None: no preview
    distance: float | None  # close / 210-day average - 1, on the preview's day
    held: Decimal | None  # GBP at the latest run's quotes; None: that run has no snapshot
    weight_now: float | None  # share of equity; None as above
    weight_preview: float | None  # what the month-end rule would hold on the preview's day


@dataclass(frozen=True, slots=True)
class DecisionRow:
    ticker: str
    side: str  # "buy" or "sell"
    proposed: Decimal | None  # GBP; None: no quote to value it
    approved: Decimal | None  # GBP; None: refused, or no quote
    reason: str
    refused: bool = False


@dataclass(frozen=True, slots=True)
class Change:
    day: date
    text: str  # e.g. "deploy 1a2b3c4"


@dataclass(frozen=True, slots=True)
class WeeklyInput:
    values: Sequence[DayValue]  # one per trading day since setup, oldest first
    seed: Decimal
    started: date  # paper trading's first day
    today: date
    funds: Sequence[FundRow]
    preview_as_of: date | None
    next_month_end: date
    decisions_day: date | None  # the last month end the bot acted on
    decisions: Sequence[DecisionRow]
    changes: Sequence[Change]
    drawdown_halve: Decimal
    drawdown_stop: Decimal
    mark: Decimal | None = None  # the ledger's high-water mark, as the risk layer uses it


def buy_and_hold(seed: Decimal, start: dict[str, Decimal], now: dict[str, Decimal]) -> Decimal | None:
    """The seed split equally over `start`'s instruments at their start prices, held: its
    value at `now`'s prices. None if any price is missing."""
    if not start or set(start) - set(now):
        return None
    share = seed / len(start)
    return sum((share * now[t] / start[t] for t in start), Decimal(0))


# ---- formatting -------------------------------------------------------------------------


def _gbp(amount: Decimal, *, pence: bool = False) -> str:
    return f"£{amount:,.2f}" if pence else f"£{amount:,.0f}"


def _signed_gbp(amount: Decimal) -> str:
    return f"{'+' if amount >= 0 else MINUS}£{abs(amount):,.2f}"


def _pct(x: float, *, signed: bool = True, places: int = 1) -> str:
    shown = f"{abs(x) * 100:.{places}f}"
    if float(shown) == 0:
        return f"{shown}%"
    sign = ("+" if x >= 0 else MINUS) if signed else (MINUS if x < 0 else "")
    return f"{sign}{shown}%"


def _delta(amount: Decimal, base: Decimal, label: str) -> str:
    ratio = float(amount / base) if base else 0.0
    cls = "up" if amount >= 0 else "down"
    arrow = "▲" if amount >= 0 else "▼"
    return (
        f'<span class="delta {cls}"><span aria-hidden=true>{arrow}</span> {_signed_gbp(amount)} '
        f"({_pct(ratio, places=2)})</span> <span class=muted>{escape(label)}</span>"
    )


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


# ---- the hero ---------------------------------------------------------------------------


def hero(w: WeeklyInput) -> str:
    """The equity figure, with today's and since-setup changes and the benchmark beside it."""
    if not w.values:
        return ""
    last = w.values[-1]
    prev = w.values[-2].bot if len(w.values) > 1 else w.seed
    parts = [
        f"<p class=hero-label>Account value · {_day(last.day)}</p>",
        f"<p class=hero>{_gbp(last.bot, pence=True)}</p>",
        f"<p class=deltas>{_delta(last.bot - prev, prev, 'today')}<br>"
        f"{_delta(last.bot - w.seed, w.seed, 'since setup')}</p>",
    ]
    start = _start(w, "benchmark")
    if last.benchmark is not None and start is not None:
        first, base = start
        bot_base = w.seed if first == w.values[0] else first.bot
        gap = float(last.bot / bot_base - last.benchmark / base)
        word = "ahead of" if gap >= 0 else "behind"
        parts.append(
            f"<p class=muted>{_pct(abs(gap), signed=False)} {word} SSAC (the MSCI ACWI tracker) "
            f"{_since(w, first.day)}</p>"
        )
    return "".join(parts)


def _start(w: WeeklyInput, field: str) -> tuple[DayValue, Decimal] | None:
    """A benchmark's first day on the chart, and its value then."""
    for v in w.values:
        value = getattr(v, field)
        if value is not None:
            return v, value
    return None


def _since(w: WeeklyInput, day: date) -> str:
    """Benchmarks added after setup are compared from their own first day, and say so."""
    return "since setup" if w.values and day == w.values[0].day else f"since {day.day} {day:%b}"


# ---- value over time --------------------------------------------------------------------

_W, _H = 340, 190
_L, _R, _T, _B = 44, 10, 10, 22


def _ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    span = hi - lo or 1.0
    raw = span / count
    magnitude = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1.0
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)  # whole pounds
    start = (lo // step) * step
    out = []
    v = start
    while v <= hi + step * 0.001:
        if v >= lo - step * 0.001:
            out.append(v)
        v += step
    return out or [lo, hi]  # series within a pound of each other: label the ends


def _x_positions(n: int, width: float = _W) -> list[float]:
    inner = width - _L - _R
    return [_L + (inner * i / (n - 1) if n > 1 else inner / 2) for i in range(n)]


def _x_labels(days: Sequence[date], xs: list[float], height: float) -> str:
    picks = sorted({0, len(days) - 1} | ({len(days) // 2} if len(days) >= MIDDLE_LABEL_FROM else set()))
    out = []
    for i in picks:
        anchor = "start" if i == 0 else "end" if i == len(days) - 1 else "middle"
        out.append(
            f'<text class=axis x="{xs[i]:.1f}" y="{height - 6}" text-anchor={anchor}>'
            f"{days[i].day} {days[i]:%b}</text>"
        )
    return "".join(out)


def _hits(xs: list[float], height: float, title: list[str]) -> str:
    """One transparent column per day, wider than the marks: its title is the hover (or tap)."""
    half = (xs[1] - xs[0]) / 2 if len(xs) > 1 else 20
    return "".join(
        f'<rect class=hit x="{x - half:.1f}" y="0" width="{2 * half:.1f}" height="{height - _B}">'
        f"<title>{escape(t)}</title></rect>"
        for x, t in zip(xs, title, strict=True)
    )


def value_chart(w: WeeklyInput) -> str:
    vals = list(w.values)
    if len(vals) < 2:  # noqa: PLR2004 - a line needs two days
        return "<p class=muted>The chart starts after the second run.</p>"
    series: dict[str, list[float | None]] = {
        "bot": [float(v.bot) for v in vals],
        "ew": [float(v.equal_weight) if v.equal_weight is not None else None for v in vals],
        "bm": [float(v.benchmark) if v.benchmark is not None else None for v in vals],
    }
    seen = [x for s in series.values() for x in s if x is not None] + [float(w.seed)]
    pad = (max(seen) - min(seen)) * 0.08 or float(w.seed) * 0.01
    lo, hi = min(seen) - pad, max(seen) + pad
    ticks = _ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    xs = _x_positions(len(vals))

    def y(v: float) -> float:
        return _T + (hi - v) / (hi - lo) * (_H - _T - _B)

    places = 0 if all(t == int(t) for t in ticks) else 2
    grid = "".join(
        f'<line class=grid x1="{_L}" x2="{_W - _R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
        f'<text class=axis x="{_L - 6}" y="{y(t) + 4:.1f}" text-anchor=end>£{t:,.{places}f}</text>'
        for t in ticks
    )
    cash = (
        f'<line class=cash x1="{_L}" x2="{_W - _R}" y1="{y(float(w.seed)):.1f}" y2="{y(float(w.seed)):.1f}"/>'
    )
    lines, ends = [], []
    for key in ("bm", "ew", "bot"):  # the bot on top
        points = [(x, v) for x, v in zip(xs, series[key], strict=True) if v is not None]
        if len(points) < 2:  # noqa: PLR2004
            continue
        path = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in points)
        lines.append(f'<polyline class="line s-{key}" points="{path}"/>')
        x, v = points[-1]
        ends.append(f'<circle class="end s-{key}" cx="{x:.1f}" cy="{y(v):.1f}" r="4"/>')
    changes = []
    by_day = {v.day: i for i, v in enumerate(vals)}
    for c in w.changes:
        if c.day in by_day:
            x = xs[by_day[c.day]]
            changes.append(
                f'<path class=change d="M{x - 4:.1f},{_H - _B + 9} l4,-7 l4,7 z">'
                f"<title>{escape(_day(c.day))}: "
                f"{escape(c.text)}</title></path>"
            )
    titles = [
        f"{_day(v.day)}: bot {_gbp(v.bot, pence=True)}"
        + (f" · 1/N {_gbp(v.equal_weight, pence=True)}" if v.equal_weight is not None else "")
        + (f" · SSAC {_gbp(v.benchmark, pence=True)}" if v.benchmark is not None else "")
        + f" · cash {_gbp(w.seed, pence=True)}"
        for v in vals
    ]
    svg = (
        f'<svg viewBox="0 0 {_W} {_H}" role=img '
        'aria-label="Value since setup: the bot against 1/N, SSAC and cash">'
        f"{grid}{cash}{''.join(lines)}{''.join(ends)}{''.join(changes)}"
        f"{_x_labels([v.day for v in vals], xs, _H)}{_hits(xs, _H, titles)}</svg>"
    )
    last = vals[-1]

    def item(cls: str, name: str, value: Decimal | None, base: Decimal | None) -> str:
        figure = (
            f"<b>{_gbp(value)}</b> {_pct(float(value / base - 1))}"
            if value is not None and base
            else "<b>—</b>"
        )
        return f"<li><i class='key {cls}'></i>{escape(name)} {figure}</li>"

    def base(field: str) -> Decimal | None:
        start = _start(w, field)
        return None if start is None else (w.seed if start[0] == vals[0] else start[1])

    late = [s[0].day for f in ("equal_weight", "benchmark") if (s := _start(w, f)) and s[0] != vals[0]]
    note = (
        f"<p class=small>1/N and SSAC start {_since(w, min(late))[6:]} at the bot's value; "
        "% is from each line's start.</p>"
        if late
        else ""
    )

    legend = (
        "<ul class=legend>"
        + item("s-bot", "Bot", last.bot, w.seed)
        + item("s-ew", "1/N", last.equal_weight, base("equal_weight"))
        + item("s-bm", "SSAC", last.benchmark, base("benchmark"))
        + item("cash", "Cash", w.seed, w.seed)
        + "</ul>"
    )
    return legend + svg + note


# ---- drawdown ---------------------------------------------------------------------------

_DH = 120


def drawdowns(w: WeeklyInput) -> list[float]:
    peak = float(w.seed)
    out = []
    for v in w.values:
        peak = max(peak, float(v.bot))
        out.append(float(v.bot) / peak - 1)
    return out


def drawdown_chart(w: WeeklyInput) -> str:
    vals = list(w.values)
    if not vals:
        return ""
    dd = drawdowns(w)
    halve, stop = -float(w.drawdown_halve), -float(w.drawdown_stop)
    lo = min(min(dd) * 1.1, stop * 1.15)
    xs = _x_positions(len(vals)) if len(vals) > 1 else [float(_L), float(_W - _R)]
    dd_plot = dd if len(vals) > 1 else dd * 2

    def y(v: float) -> float:
        return _T + (0 - v) / (0 - lo) * (_DH - _T - _B)

    path = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, dd_plot, strict=True))
    area = f'<polygon class=under points="{xs[0]:.1f},{y(0):.1f} {path} {xs[-1]:.1f},{y(0):.1f}"/>'
    line = f'<polyline class="line s-bot" points="{path}"/>'
    zero = f'<line class=base x1="{_L}" x2="{_W - _R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
    zero_label = f'<text class=axis x="{_L - 6}" y="{y(0) + 4:.1f}" text-anchor=end>0%</text>'
    limits = "".join(
        f'<line class="limit {cls}" x1="{_L}" x2="{_W - _R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
        f'<text class=axis x="{_L - 6}" y="{y(v) + 4:.1f}" text-anchor=end>{_pct(v, places=0)}</text>'
        f'<text class=note x="{_W - _R}" y="{y(v) - 5:.1f}" text-anchor=end>{escape(label)}</text>'
        for v, cls, label in ((halve, "serious", "position cap halves"), (stop, "critical", "no new buys"))
    )
    titles = [f"{_day(v.day)}: {_pct(d)} from the high" for v, d in zip(vals, dd, strict=True)]
    hits = _hits(xs[: len(vals)], _DH, titles)
    return (
        f'<svg viewBox="0 0 {_W} {_DH}" role=img aria-label="Drawdown from the high, with the risk limits">'
        f"{limits}{area}{zero}{zero_label}{line}"
        f"{_x_labels([v.day for v in vals], xs, _DH) if len(vals) > 1 else ''}{hits}</svg>"
    )


def drawdown_summary(w: WeeklyInput) -> str:
    dd = drawdowns(w)
    now = dd[-1] if dd else 0.0
    if w.mark and w.values:  # the risk layer's own figure, from the ledger's high-water mark
        now = min(0.0, float(w.values[-1].bot / w.mark) - 1)
    worst = min([*dd, now]) if dd else 0.0
    room = max(0.0, float(w.drawdown_halve) + now)
    return (
        f"<p class=stat-row><span><b>{_pct(now, places=1)}</b> <span class=muted>now</span></span>"
        f"<span><b>{_pct(worst, places=1)}</b> <span class=muted>worst</span></span>"
        f"<span><b>{_pct(room, signed=False, places=1)}</b> "
        "<span class=muted>room before halving</span></span></p>"
    )


# ---- funds ------------------------------------------------------------------------------


def _distance_bar(distance: float | None, in_trend: bool | None) -> str:
    w, h, mid = 96, 12, 48
    axis = f'<line class=base x1="{mid}" x2="{mid}" y1="0" y2="{h}"/>'
    if distance is None:
        return f'<svg class=dist viewBox="0 0 {w} {h}" aria-hidden=true>{axis}</svg>'
    clamped = max(-DISTANCE_SCALE, min(DISTANCE_SCALE, distance))
    length = max(abs(clamped) / DISTANCE_SCALE * (mid - 1), 2.0)
    x = mid if clamped >= 0 else mid - length
    cls = "in" if in_trend else "out"
    # rounded at the data end only: the baseline end is squared off by a second rect
    square_x = x if clamped >= 0 else x + max(length - 4, 0)
    bar = (
        f'<rect class="bar {cls}" x="{x:.1f}" y="2" width="{length:.1f}" height="8" rx="4"/>'
        f'<rect class="bar {cls}" x="{square_x:.1f}" y="2" width="{min(4, length):.1f}" height="8"/>'
    )
    return f'<svg class=dist viewBox="0 0 {w} {h}" aria-hidden=true>{bar}{axis}</svg>'


def _trend_mark(in_trend: bool | None) -> str:
    if in_trend is None:
        return "<span class='trend none' aria-label='no preview'>·</span>"
    if in_trend:
        return "<span class='trend in' aria-label='in trend'>▲</span>"
    return "<span class='trend out' aria-label='out of trend'>▽</span>"


def funds(w: WeeklyInput) -> str:
    """One compact row per fund: trend and distance to the 210-day average, what's held now,
    and what the month-end rule would hold on the latest closes."""
    rows = []
    for f in w.funds:
        distance = _pct(f.distance) if f.distance is not None else "—"
        preview = _pct(f.weight_preview, signed=False, places=0) if f.weight_preview is not None else "—"
        held = (
            f"{_gbp(f.held)}<span class=small>{_pct(f.weight_now, signed=False, places=0)}</span>"
            if f.held and f.weight_now is not None
            else "<span class=muted>—</span>"
        )
        rows.append(
            f"<div class=fund title='{escape(FUND_NAMES.get(f.ticker, f.ticker))}'>"
            f"<div>{_trend_mark(f.in_trend)}<b>{escape(f.ticker)}</b>"
            f"<span class=small>{escape(FUND_NAMES.get(f.ticker, ''))}</span></div>"
            f"<div>{_distance_bar(f.distance, f.in_trend)}<span class=small>{distance}</span></div>"
            f"<div class=num>{held}</div><div class=num><b>{preview}</b></div></div>"
        )
    in_trend = sum(1 for f in w.funds if f.in_trend)
    as_of = f" on {_day(w.preview_as_of)}'s closes" if w.preview_as_of else ""
    head = (
        f"<p><b>{in_trend} of {len(w.funds)}</b> in trend{as_of} "
        "<span class=muted>(above their 210-day average; out of trend is held as cash)</span></p>"
    )
    columns = (
        "<div class='fund th'><div>Fund</div><div>vs 210-day avg</div><div class=num>Held</div>"
        "<div class=num>Month end</div></div>"
    )
    return head + f"<div class=funds>{columns}{''.join(rows)}</div>"


def _split_bar(invested: float, label: str) -> str:
    invested = max(0.0, min(1.0, invested))
    w = 340
    gap = 2 if 0 < invested < 1 else 0
    a = (w - gap) * invested
    parts = []
    if a > 0:
        parts.append(f'<rect class="bar in" x="0" y="0" width="{a:.1f}" height="10" rx="4"/>')
    if invested < 1:
        parts.append(
            f'<rect class="bar cashfill" x="{a + gap:.1f}" y="0" width="{w - a - gap:.1f}" '
            'height="10" rx="4"/>'
        )
    return (
        f"<div class=split><span class=small>{escape(label)}</span>"
        f'<svg viewBox="0 0 {w} 10" aria-hidden=true>{"".join(parts)}</svg>'
        f"<span class=small><b>{_pct(invested, signed=False, places=0)}</b> invested · "
        f"<b>{_pct(1 - invested, signed=False, places=0)}</b> cash</span></div>"
    )


def month_end(w: WeeklyInput) -> str:
    days = (w.next_month_end - w.today).days
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    parts = [
        f"<p>Next month end <b>{_day(w.next_month_end)}</b> "
        f"<span class=muted>({when}); orders the next morning</span></p>"
    ]
    if w.funds and all(f.weight_now is not None for f in w.funds):
        parts.append(_split_bar(sum(f.weight_now or 0 for f in w.funds), "Now"))
    else:
        parts.append("<p class=small>Now: unknown (the latest run stored no holdings)</p>")
    if all(f.weight_preview is not None for f in w.funds) and w.funds:
        parts.append(_split_bar(sum(f.weight_preview or 0 for f in w.funds), "If today were the month end"))
    return "".join(parts)


# ---- decisions, progress, changes -------------------------------------------------------


def decisions(w: WeeklyInput) -> str:
    if w.decisions_day is None:
        return "<p class=muted>No month end acted on yet: the first orders follow the next month end.</p>"
    if not w.decisions:
        return f"<p class=muted>{_day(w.decisions_day)}: nothing to trade.</p>"
    rows = []
    for d in w.decisions:
        if d.refused:
            outcome, cls = "refused", "critical"
        elif d.approved is not None and d.proposed is not None and d.approved < d.proposed:
            outcome, cls = f"cut to {_gbp(d.approved)}", "warning"
        else:
            outcome, cls = "approved", "good"
        icon = {"good": "✓", "warning": "!", "critical": "✕"}[cls]
        proposed = _gbp(d.proposed) if d.proposed is not None else "—"
        rows.append(
            f"<tr><td><b>{escape(d.ticker)}</b></td><td>{escape(d.side)} {proposed}</td>"
            f"<td><span class='status {cls}' aria-hidden=true>{icon}</span> {escape(outcome)}"
            f"<div class=small>{escape(d.reason)}</div></td></tr>"
        )
    head = (
        f"<p class=muted>Risk decisions at the last month end, {_day(w.decisions_day)} "
        "(what risk allowed; the glance and runs show what was sent)</p>"
    )
    return f"{head}<table class=dec>{''.join(rows)}</table>"


def paper_progress(w: WeeklyInput) -> str:
    day = max(1, min(PAPER_DAYS, (w.today - w.started).days + 1))
    end = w.started + timedelta(days=PAPER_DAYS - 1)
    share = day / PAPER_DAYS
    width = 340
    filled = max(8.0, width * share)
    return (
        f"<p><b>Day {day}</b> of {PAPER_DAYS} <span class=muted>· ends {_day(end)} {end.year}</span></p>"
        f'<svg viewBox="0 0 {width} 8" aria-hidden=true>'
        f'<rect class=track x="0" y="0" width="{width}" height="8" rx="4"/>'
        f'<rect class="bar in" x="0" y="0" width="{filled:.1f}" height="8" rx="4"/></svg>'
    )


def numbers_table(w: WeeklyInput) -> str:
    dd = drawdowns(w)
    rows = "".join(
        f"<tr><td>{_day(v.day)}</td><td>{_gbp(v.bot)}</td>"
        f"<td>{_gbp(v.equal_weight) if v.equal_weight is not None else '—'}</td>"
        f"<td>{_gbp(v.benchmark) if v.benchmark is not None else '—'}</td><td>{_pct(d)}</td></tr>"
        for v, d in zip(reversed(w.values), reversed(dd), strict=True)
    )
    return (
        "<details><summary>The numbers</summary><table class=nums><tr><th>Day</th><th>Bot</th><th>1/N</th>"
        f"<th>SSAC</th><th>From high</th></tr>{rows}</table></details>"
    )


def changes(w: WeeklyInput) -> str:
    if not w.changes:
        return ""
    items = "; ".join(f"{c.day.day} {c.day:%b} {escape(c.text)}" for c in w.changes)
    return f"<p class=small>▲ on the axis: {items}</p>"


STYLE = (
    ":root{--plane:#f4f3ef;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6d68;--grid:#e1e0d9;"
    "--base:#c3c2b7;--ring:rgba(11,11,11,.08);--s-bot:#2a78d6;--s-ew:#eb6834;--s-bm:#1baf7a;"
    "--out:#a8a69f;--cashfill:#e1e0d9;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;"
    "--up:#006300;--down:#b3261e;--under:rgba(42,120,214,.14);color-scheme:light}"
    "@media (prefers-color-scheme:dark){:root{--plane:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;"
    "--muted:#9a9890;--grid:#2c2c2a;--base:#4a4945;--ring:rgba(255,255,255,.08);--s-bot:#3987e5;"
    "--s-ew:#d95926;"
    "--s-bm:#199e70;--out:#6b6a65;--cashfill:#383835;--up:#0ca30c;--down:#e66767;"
    "--under:rgba(57,135,229,.22);"
    "color-scheme:dark}}"
    "svg{display:block;width:100%;height:auto;overflow:visible}"
    "svg text{font:11px system-ui,sans-serif;font-variant-numeric:tabular-nums}"
    ".axis{fill:var(--muted)}.note{fill:var(--ink2)}"
    ".grid{stroke:var(--grid);stroke-width:1}.base{stroke:var(--base);stroke-width:1}"
    ".line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
    ".line.s-bot{stroke:var(--s-bot)}.line.s-ew{stroke:var(--s-ew)}.line.s-bm{stroke:var(--s-bm)}"
    ".end{stroke:var(--card);stroke-width:2}.end.s-bot{fill:var(--s-bot)}.end.s-ew{fill:var(--s-ew)}"
    ".end.s-bm{fill:var(--s-bm)}"
    ".cash{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:4 4}"
    ".under{fill:var(--under)}"
    ".limit{stroke-width:1.5;stroke-dasharray:5 3}.limit.serious{stroke:var(--serious)}"
    ".limit.critical{stroke:var(--critical)}"
    ".change{fill:var(--ink2)}"
    ".hit{fill:transparent}.hit:hover{fill:var(--ring)}"
    ".bar.in{fill:var(--s-bot)}.bar.out{fill:var(--out)}.bar.cashfill{fill:var(--cashfill)}"
    ".track{fill:var(--cashfill)}"
    ".hero-label{margin:0;color:var(--ink2);font-size:14px}"
    ".hero{margin:2px 0 6px;font-size:40px;font-weight:650;letter-spacing:-.02em;"
    "font-variant-numeric:tabular-nums;line-height:1.1}"
    ".deltas{margin:0 0 8px;font-variant-numeric:tabular-nums}"
    ".delta{font-weight:600}.delta.up{color:var(--up)}.delta.down{color:var(--down)}"
    ".legend{display:flex;flex-wrap:wrap;gap:4px 14px;list-style:none;padding:0;margin:0 0 8px;"
    "font-size:13px;"
    "font-variant-numeric:tabular-nums}"
    ".legend li{border:0;padding:0;white-space:nowrap}"
    ".key{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:5px;"
    "vertical-align:middle}"
    ".key.s-bot{background:var(--s-bot)}.key.s-ew{background:var(--s-ew)}.key.s-bm{background:var(--s-bm)}"
    ".key.cash{background:repeating-linear-gradient(90deg,var(--muted) 0 3px,transparent 3px 6px)}"
    ".stat-row{display:flex;justify-content:space-between;gap:8px;margin:0 0 6px;"
    "font-variant-numeric:tabular-nums}"
    ".stat-row>span{display:flex;flex-direction:column;font-size:13px}.stat-row b{font-size:18px}"
    ".funds{display:grid;font-variant-numeric:tabular-nums}"
    ".fund{display:grid;grid-template-columns:1fr 96px 52px 44px;gap:10px;align-items:center;padding:8px 0;"
    "border-bottom:1px solid var(--grid)}.fund:last-child{border:0}"
    ".fund>div{display:flex;flex-direction:column;min-width:0}.fund>div:first-child{display:block}"
    ".fund.th{font-size:12px;color:var(--muted);padding-top:0}.num{text-align:right;align-items:flex-end}"
    ".fund .small{display:block;font-size:12px;line-height:1.3}"
    ".trend{display:inline-block;width:16px;font-size:12px}.trend.in{color:var(--s-bot)}"
    ".trend.out,.trend.none{color:var(--muted)}"
    ".fund .dist{margin:3px 0 2px}.fund>div:nth-child(2){align-items:center}"
    ".small{font-size:13px;color:var(--ink2);font-variant-numeric:tabular-nums}"
    ".split{display:grid;gap:4px;margin:10px 0}"
    ".dec{width:100%;border-collapse:collapse;font-size:14px}.dec td{padding:6px 6px 6px 0;"
    "vertical-align:top;"
    "border-bottom:1px solid var(--grid);white-space:normal}"
    "details{margin-top:10px;font-size:14px}summary{cursor:pointer;color:var(--ink2)}"
    ".nums{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums;"
    "margin-top:6px}"
    ".nums th,.nums td{text-align:right;padding:3px 0 3px 6px;border-bottom:1px solid var(--grid)}"
    ".nums th:first-child,.nums td:first-child{text-align:left;padding-left:0}"
)


def weekly_html(w: WeeklyInput) -> tuple[str, str]:
    """The hero, and the weekly sections (each a card), as two HTML fragments."""
    cards = [
        ("Value since setup", value_chart(w) + numbers_table(w) + changes(w)),
        ("Drawdown", drawdown_summary(w) + drawdown_chart(w)),
        ("Funds", funds(w)),
        ("Month end", month_end(w) + decisions(w)),
        ("Paper trading", paper_progress(w)),
    ]
    body = "".join(f"<section class=card><h2>{escape(t)}</h2>{c}</section>" for t, c in cards)
    return hero(w), body
