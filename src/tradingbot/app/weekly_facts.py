"""The weekly view's figures, read from the ledger (dashboard slice 2).

Read-only, like `glance_facts`: every figure comes from what the runs recorded (equity,
snapshots, previews, decisions and the closes each run read), so the page never computes a
signal or fetches a price itself. A ledger from before the closes were recorded still shows
the bot's value; the benchmark lines start on the first day with closes.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from tradingbot.domain.market import BENCHMARK, Instrument
from tradingbot.ledger.store import Ledger, LedgerError, LevelRecord, RunRecord
from tradingbot.report.weekly import (
    Change,
    DayValue,
    DecisionRow,
    FundRow,
    WeeklyInput,
    buy_and_hold,
)
from tradingbot.risk.limits import Limits

VERSION_SHOWN = 7  # characters of the deployed commit


def _closes(levels: list[LevelRecord]) -> dict[str, Decimal]:
    """Each instrument's total-return index: its close over its close on the anchor day, both
    from one adjusted fetch, so dividends count however the history was later rescaled."""
    return {r.instrument.ticker: Decimal(repr(r.close)) / Decimal(repr(r.anchor)) for r in levels if r.anchor}


def _values(
    ledger: Ledger,
    runs: list[RunRecord],
    levels: Mapping[str, list[LevelRecord]],
    seed: Decimal,
    funds: list[str],
) -> list[DayValue]:
    by_day: dict[date, dict[str, Decimal]] = {}
    for run in runs:  # the day's last run with closes wins
        if run.run_id in levels:
            by_day[run.started_at.date()] = _closes(levels[run.run_id])
    values: list[DayValue] = []
    start_funds: tuple[Decimal, dict[str, Decimal]] | None = None
    start_ssac: tuple[Decimal, dict[str, Decimal]] | None = None
    for day in sorted({r.started_at.date() for r in runs}):
        equity = ledger.equity_on(day)
        if equity is None:
            continue
        bot = equity.amount
        closes = by_day.get(day, {})
        fund_closes = {t: closes[t] for t in funds if t in closes}
        if start_funds is None and len(fund_closes) == len(funds):
            start_funds = (bot if values else seed, fund_closes)  # from the bot's value that day
        ssac = {BENCHMARK.ticker: closes[BENCHMARK.ticker]} if BENCHMARK.ticker in closes else {}
        if start_ssac is None and ssac:
            start_ssac = (bot if values else seed, ssac)
        values.append(
            DayValue(
                day,
                bot,
                buy_and_hold(start_funds[0], start_funds[1], fund_closes) if start_funds else None,
                buy_and_hold(start_ssac[0], start_ssac[1], ssac) if start_ssac else None,
            )
        )
    return values


def _funds(
    ledger: Ledger, run_id: str | None, levels: list[LevelRecord], universe: list[Instrument]
) -> list[FundRow]:
    preview = {r.instrument.ticker: r for r in ledger.preview(run_id)} if run_id else {}
    level = {r.instrument.ticker: r for r in levels}
    held: dict[str, Decimal] | None = None  # unknown without a snapshot: never "all cash"
    equity = Decimal(0)
    if run_id:
        try:
            snap = ledger.snapshot(run_id)
        except LedgerError:
            snap = None
        if snap is not None:
            held = {
                p.instrument.ticker: p.value_gbp.amount for p in snap.positions if p.value_gbp is not None
            }
            equity = snap.cash.amount + sum(held.values(), Decimal(0))
    rows = []
    for i in universe:
        p, lv = preview.get(i.ticker), level.get(i.ticker)
        value = held.get(i.ticker, Decimal(0)) if held is not None else None
        rows.append(
            FundRow(
                i.ticker,
                p.in_trend if p else None,
                lv.close / lv.average - 1 if lv and lv.average else None,
                value,
                float(value / equity) if value is not None and equity else None,
                p.weight if p else None,
            )
        )
    return rows


def _decisions(ledger: Ledger, runs: list[RunRecord]) -> tuple[date | None, list[DecisionRow]]:
    for run in reversed(runs):
        records = ledger.decisions(run.run_id)
        if records:
            rows = []
            for d in records:
                price = d.quote.to_gbp().amount if d.quote else None
                approved = (
                    d.approved_quantity * price
                    if d.approved_quantity is not None and price is not None
                    else None
                )
                rows.append(
                    DecisionRow(
                        d.instrument.ticker,
                        d.side.value.lower(),
                        d.proposed_quantity * price if price is not None else None,
                        approved,
                        d.reason,
                        refused=d.approved_quantity is None,
                    )
                )
            return run.started_at.date(), rows
    return None, []


def _changes(runs: list[RunRecord]) -> list[Change]:
    out = []
    for before, after in itertools.pairwise(runs):
        if after.version != before.version:
            out.append(Change(after.started_at.date(), f"deploy {after.version[:VERSION_SHOWN]}"))
    return out


def weekly_input(
    ledger: Ledger,
    *,
    today: date,
    universe: Mapping[Instrument, Decimal],
    limits: Limits,
    next_month_end: date,
) -> WeeklyInput | None:
    """None before setup (no seeded mark) or before the first run. Funds come from the latest
    run that stored levels; held amounts only from that run's own snapshot."""
    seed = ledger.first_high_water_mark()
    runs = sorted(ledger.runs(), key=lambda r: r.started_at)
    if seed is None or not runs:
        return None
    levels = ledger.levels()
    funds = list(universe)
    latest = next((r.run_id for r in reversed(runs) if r.run_id in levels), None)
    preview_as_of = next((r.as_of for r in levels.get(latest or "", []) if r.instrument in universe), None)
    decisions_day, decisions = _decisions(ledger, runs)
    return WeeklyInput(
        values=_values(ledger, runs, levels, seed.amount, [i.ticker for i in funds]),
        seed=seed.amount,
        started=runs[0].started_at.date(),
        today=today,
        funds=_funds(ledger, latest, levels.get(latest or "", []), funds),
        preview_as_of=preview_as_of,
        next_month_end=next_month_end,
        decisions_day=decisions_day,
        decisions=decisions,
        changes=_changes(runs),
        drawdown_halve=limits.drawdown_halve,
        drawdown_stop=limits.drawdown_stop,
        mark=mark.amount if (mark := ledger.high_water_mark()) else None,
    )
