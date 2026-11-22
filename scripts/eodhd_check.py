#!/usr/bin/env python3
"""Owner-run: check EODHD's free tier against the bot's funds (Phase 2 step 3; ADR-0018).

Usage (Cloud Shell or your machine, from the repo root):
    PYTHONPATH=src python3 scripts/eodhd_check.py
It asks for the API key (hidden), fetches about 13 months of daily prices for each traded
fund and the SSAC benchmark through the bot's own adapter, and prints, per fund: how many
bars came back, the first and last date, the last close and whether it looks like pence or
pounds. Public market data only: safe to paste. The key is never printed.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from tradingbot.app.eodhd import EodhdHistory
from tradingbot.app.storage import urllib_transport
from tradingbot.config.load import load_config
from tradingbot.data.prices import DataError, PriceHistory
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency

LOOKBACK_DAYS = 400  # the bot asks for 450; the free tier is said to give one year
PENCE_LIKELY_ABOVE = 200.0  # these ETFs trade at tens of pounds, or thousands of pence


def report(source: PriceHistory, funds: Sequence[Instrument], today: date) -> list[str]:
    lines = [
        f"{'fund':6} {'bars':>5} {'first':>10} {'last':>10} {'last close':>11}  units (configured / looks like)"
    ]
    for fund in funds:
        try:
            bars = source.daily_bars(fund, today - timedelta(days=LOOKBACK_DAYS), today - timedelta(days=1))
        except DataError as error:
            lines.append(f"{fund.ticker:6} error: {error}")
            continue
        if not bars:
            lines.append(f"{fund.ticker:6} no bars")
            continue
        last = bars[-1]
        looks = "GBX" if last.close > PENCE_LIKELY_ABOVE else "GBP"
        flag = "" if looks == fund.currency.value else "  <-- check"
        lines.append(
            f"{fund.ticker:6} {len(bars):>5} {bars[0].day!s:>10} {last.day!s:>10} {last.close:>11.4f}"
            f"  {fund.currency.value} / {looks}{flag}"
        )
    return lines


def main() -> int:
    key = getpass.getpass("EODHD API key (hidden): ").strip()
    if not key:
        print("no key given", file=sys.stderr)
        return 2
    funds = [
        *load_config().universe,
        Instrument("SSAC", Currency.GBP),
    ]  # SSAC: the benchmark's unit is a guess
    source = EodhdHistory(urllib_transport(timeout=30.0), key)
    print("\n".join(report(source, funds, datetime.now(UTC).date())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
