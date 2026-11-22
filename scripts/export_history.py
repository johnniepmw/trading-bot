#!/usr/bin/env python3
"""Export adjusted daily price history for the strategy's backtest (Phase 1, step 8).

Owner-run, in Google Cloud Shell. Public market data only: no keys, no account data.
Standard library only (Python 3.11+).

    python3 scripts/export_history.py --list            # first/last date and currency per symbol; writes nothing
    python3 scripts/export_history.py                    # export to ~/history-export (see below)
    gcloud storage cp -r ~/history-export gs://<your-private-bucket>/history-<date>   # never the repo

The export:
- reads the universe from research/universe.toml;
- downloads daily bars from Yahoo Finance and uses the adjusted (total-return) close, with
  the open adjusted by the same factor, so opens and closes match;
- splices each fund's proxies (older funds on the same index) under its own history, scaled
  by the price ratio on their first common day;
- cross-checks daily returns against Stooq and refuses to write if they disagree on too
  many days (--accept-disagreement overrides, and is recorded in the manifest);
- writes the development period and the hold-out period to separate folders, plus a
  manifest with sources, splice dates, row counts and SHA-256 hashes.
Only the final step-8 run is given the hold-out folder (docs/strategies/trend-long-or-cash.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import sys
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ROOT / "research" / "universe.toml"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2={end}&interval=1d&events=div%2Csplit"
STOOQ = "https://stooq.com/q/d/l/?s={symbol}&i=d"
LONDON = ZoneInfo("Europe/London")
RETURN_TOLERANCE = 0.02  # a day's return may differ by this much between sources (dividend days do)
MAX_DISAGREEING_SHARE = 0.01  # more than 1% of common days disagreeing fails the export
CURRENCIES = {"GBp": "GBX", "GBX": "GBX", "GBP": "GBP"}

Opener = Callable[[str], bytes]


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class Fund:
    ticker: str
    role: str
    yahoo: str
    proxies: list[str]
    stooq: str


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    close: float


@dataclass
class Series:
    symbol: str
    currency: str
    bars: list[Bar] = field(default_factory=list)


def default_opener(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ExportError(f"refusing a non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (history export)"})  # noqa: S310 - https only, checked above
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https hosts
        return response.read()


def load_universe(path: Path = UNIVERSE) -> tuple[list[Fund], date]:
    raw = tomllib.loads(path.read_text())
    funds = [Fund(f["ticker"], f["role"], f["yahoo"], list(f["proxies"]), f["stooq"]) for f in raw["fund"]]
    return funds, date.fromisoformat(raw["hold_out_start"])


def fetch_yahoo(symbol: str, opener: Opener) -> Series:
    url = YAHOO.format(symbol=urllib.parse.quote(symbol), end=int(datetime.now(UTC).timestamp()))
    try:
        result = json.loads(opener(url))["chart"]["result"][0]
        currency = CURRENCIES[result["meta"]["currency"]]
        quote, adjusted = result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]["adjclose"]
        rows = zip(result["timestamp"], quote["open"], quote["close"], adjusted, strict=True)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ExportError(f"{symbol}: unexpected Yahoo response ({error!r})") from error
    bars = []
    for stamp, open_, close, adj in rows:
        if None in (open_, close, adj) or close <= 0 or open_ <= 0 or adj <= 0:
            continue  # holidays and gaps come back as nulls
        factor = adj / close
        bars.append(Bar(datetime.fromtimestamp(stamp, LONDON).date(), open_ * factor, adj))
    if not bars:
        raise ExportError(f"{symbol}: no usable bars")
    return Series(symbol, currency, bars)


def fetch_stooq(symbol: str, opener: Opener) -> dict[date, float]:
    text = opener(STOOQ.format(symbol=urllib.parse.quote(symbol))).decode()
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise ExportError(f"{symbol}: no Stooq data ({text[:60]!r})")
    return {date.fromisoformat(r["Date"]): float(r["Close"]) for r in rows if r.get("Close")}


def _in_currency(series: Series, currency: str) -> list[Bar]:
    if series.currency == currency:
        return series.bars
    factor = {("GBP", "GBX"): 100.0, ("GBX", "GBP"): 0.01}.get((series.currency, currency))
    if factor is None:
        raise ExportError(f"{series.symbol}: can't convert {series.currency} to {currency}")
    return [Bar(b.day, b.open * factor, b.close * factor) for b in series.bars]


def splice(primary: Series, proxies: list[Series]) -> tuple[list[Bar], list[dict[str, str]]]:
    """Proxies (oldest first) under the primary, each scaled at its first common day."""
    bars, splices = list(primary.bars), []
    for proxy in reversed(proxies):
        start = bars[0].day
        proxy_bars = {b.day: b for b in _in_currency(proxy, primary.currency)}
        if start not in proxy_bars:
            raise ExportError(f"{proxy.symbol} has no bar on {start} to splice under {primary.symbol}")
        ratio = bars[0].close / proxy_bars[start].close
        earlier = [Bar(b.day, b.open * ratio, b.close * ratio) for b in proxy_bars.values() if b.day < start]
        bars = sorted(earlier, key=lambda b: b.day) + bars
        splices.append({"proxy": proxy.symbol, "until": start.isoformat(), "ratio": f"{ratio:.6f}"})
    return bars, splices


def compare(bars: list[Bar], second: dict[date, float]) -> dict[str, float]:
    """Daily returns on common days: how many differ by more than RETURN_TOLERANCE."""
    common = [b for b in bars if b.day in second]
    disagree = total = 0
    for before, after in itertools.pairwise(common):
        ours = after.close / before.close - 1
        theirs = second[after.day] / second[before.day] - 1
        total += 1
        disagree += abs(ours - theirs) > RETURN_TOLERANCE
    return {"common_days": total, "disagreeing_days": disagree, "share": disagree / total if total else 1.0}


def _write(path: Path, bars: list[Bar], currency: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "open", "close", "currency"])
        for b in bars:
            writer.writerow([b.day.isoformat(), f"{b.open:.6f}", f"{b.close:.6f}", currency])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_symbols(funds: list[Fund], opener: Opener) -> list[str]:
    lines = []
    for fund in funds:
        for symbol in [*fund.proxies, fund.yahoo]:
            try:
                s = fetch_yahoo(symbol, opener)
                lines.append(
                    f"{fund.ticker:5} {symbol:10} {s.currency} {s.bars[0].day} .. {s.bars[-1].day} ({len(s.bars)} bars)"
                )
            except ExportError as error:
                lines.append(f"{fund.ticker:5} {symbol:10} FAILED: {error}")
    return lines


def export(
    funds: list[Fund], hold_out: date, out: Path, opener: Opener, *, accept_disagreement: bool = False
) -> dict:
    manifest: dict[str, object] = {
        "created": datetime.now(UTC).isoformat(),
        "hold_out_start": hold_out.isoformat(),
        "accept_disagreement": accept_disagreement,
        "funds": {},
    }
    problems = []
    for fund in funds:
        primary = fetch_yahoo(fund.yahoo, opener)
        bars, splices = splice(primary, [fetch_yahoo(p, opener) for p in fund.proxies])
        check = compare(primary.bars, fetch_stooq(fund.stooq, opener))
        if check["share"] > MAX_DISAGREEING_SHARE:
            problems.append(
                f"{fund.ticker}: {check['disagreeing_days']} of {check['common_days']} days disagree with Stooq"
            )
        dev = [b for b in bars if b.day < hold_out]
        held = [b for b in bars if b.day >= hold_out]
        manifest["funds"][fund.ticker] = {  # type: ignore[index]
            "role": fund.role,
            "currency": primary.currency,
            "yahoo": fund.yahoo,
            "splices": splices,
            "stooq": fund.stooq,
            "cross_check": check,
            "first": bars[0].day.isoformat(),
            "last": bars[-1].day.isoformat(),
            "dev_rows": len(dev),
            "hold_out_rows": len(held),
            "dev_sha256": _write(out / "dev" / f"{fund.ticker}.csv", dev, primary.currency),
            "hold_out_sha256": _write(out / "holdout" / f"{fund.ticker}.csv", held, primary.currency),
        }
    if problems and not accept_disagreement:
        for p in out.rglob("*.csv"):
            p.unlink()
        raise ExportError("second source disagrees; nothing written:\n  " + "\n  ".join(problems))
    manifest["problems"] = problems
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None, opener: Opener = default_opener) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="show each symbol's date range; write nothing")
    parser.add_argument(
        "--out", type=Path, default=Path.home() / "history-export", help="output folder, outside the repo"
    )
    parser.add_argument("--accept-disagreement", action="store_true", help="write even if Stooq disagrees")
    args = parser.parse_args(argv)
    funds, hold_out = load_universe()
    try:
        if args.list:
            print("\n".join(list_symbols(funds, opener)))
            return 0
        if (args.out.resolve() / "").is_relative_to(
            ROOT
        ) and args.out.resolve() != ROOT / "research" / "data":
            raise ExportError(f"--out {args.out} is inside the repository: use a folder outside it")
        manifest = export(funds, hold_out, args.out, opener, accept_disagreement=args.accept_disagreement)
    except ExportError as error:
        print(f"export failed: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {len(manifest['funds'])} funds to {args.out}; upload with: gcloud storage cp -r {args.out} gs://<your-private-bucket>/"
    )  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
