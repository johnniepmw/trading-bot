#!/usr/bin/env python3
"""Export adjusted daily price history for the strategy's backtest (Phase 1, step 8).

Owner-run, in Google Cloud Shell. Public market data only: no keys, no account data.
Standard library only (Python 3.11+).

    python3 scripts/export_history.py --list      # each symbol's date range, both sources; writes nothing
    python3 scripts/export_history.py             # export to ~/history-export (must be new or empty)
    gcloud storage cp -r ~/history-export gs://<your-private-bucket>/history-<date>   # never the repo

The export:
- reads the universe and symbols from research/universe.toml (a high-risk path);
- downloads daily bars from Yahoo Finance and uses the adjusted (total-return) close, with
  the open adjusted by the same factor; drops today's partial bar and duplicate rows, and
  refuses implausible jumps or opens far from closes;
- checks each series' implied dividend yield against whether it is marked distributing
  (Yahoo's London dividends are sometimes recorded in the wrong unit);
- splices proxies (older funds on the same index) under a fund's history, scaled by the
  price ratio on their first common day;
- cross-checks every series, proxies included, against Stooq on daily returns: too many
  disagreeing days, any single day more than 10% apart, or too little coverage refuses the
  export (--accept-problems overrides; the manifest records it);
- writes the development and hold-out periods to separate folders with a manifest (sources,
  splices, checks, SHA-256 hashes), building it in a temporary folder that only becomes the
  output folder when everything succeeded. Nothing outside it is touched. (If the process
  is killed mid-run, a hidden `.history-export-*` folder may remain beside the output:
  delete it; it may hold hold-out data.)
Only the final step-8 run is given the hold-out folder (docs/strategies/trend-long-or-cash.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import math
import re
import shutil
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ROOT / "research" / "universe.toml"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2={end}&interval=1d&events=div%2Csplit"
STOOQ = "https://stooq.com/q/d/l/?s={symbol}&i=d"
ALLOWED_HOSTS = frozenset({"query1.finance.yahoo.com", "stooq.com"})
LONDON = ZoneInfo("Europe/London")
CURRENCIES = {"GBp": "GBX", "GBX": "GBX", "GBP": "GBP"}
TICKER = re.compile(r"^[A-Z0-9]{1,6}$")
SYMBOL = re.compile(r"^[A-Za-z0-9^=-]{1,12}(\.[A-Za-z]{1,3})?$")

RETURN_TOLERANCE = 0.02  # a day's return may differ by this much between sources
MAX_DISAGREEING_SHARE = 0.01  # more disagreeing days than this is a problem
HARD_DAY_LIMIT = 0.10  # any single day further apart than this is a problem (a missed split, a bad print)
MIN_COVERAGE = 0.90  # the second source must cover this share of the series' days
MAX_DAILY_RATIO = 10.0  # a 10x close-to-close move is a data error (pounds read as pence)
MAX_OPEN_CLOSE_GAP = 0.20  # opens and closes must be adjusted alike
MAX_SKIPPED_SHARE = 0.01  # more unusable rows than this is a problem
ACCUMULATING_MAX_YIELD = 0.005
DISTRIBUTING_YIELD = (0.0005, 0.10)

Opener = Callable[[str], bytes]


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    yahoo: str
    stooq: str
    distributing: bool


@dataclass(frozen=True)
class Fund:
    ticker: str
    role: str
    primary: Source
    proxies: list[Source]


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
    skipped: int = 0
    yield_: float = 0.0  # implied yearly dividend yield from the adjustment factor
    yearly: dict[int, float] = field(default_factory=dict)  # the same, year end to year end


def check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ExportError(f"refusing to fetch {url}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: PLR0913, PLR0917 - urllib's signature
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_URLOPENER = urllib.request.build_opener(_SafeRedirect)


def default_opener(url: str) -> bytes:
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (history export)"})  # noqa: S310 - https, checked
    try:
        with _URLOPENER.open(request, timeout=30) as response:
            return bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ExportError(f"fetching {url} failed: {error}") from error


def _source(raw: dict[str, Any]) -> Source:
    source = Source(str(raw["yahoo"]), str(raw["stooq"]), bool(raw["distributing"]))
    if not SYMBOL.fullmatch(source.yahoo) or not SYMBOL.fullmatch(source.stooq):
        raise ExportError(f"invalid symbol in universe: {source}")
    return source


def load_universe(path: Path = UNIVERSE) -> tuple[list[Fund], date]:
    try:
        raw = tomllib.loads(path.read_text())
        funds = [
            Fund(str(f["ticker"]), str(f["role"]), _source(f), [_source(p) for p in f["proxies"]])
            for f in raw["fund"]
        ]
        hold_out = date.fromisoformat(raw["hold_out_start"])
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        raise ExportError(f"invalid universe file: {error!r}") from error
    for fund in funds:
        if not TICKER.fullmatch(fund.ticker) or fund.role not in {"trade", "benchmark"}:
            raise ExportError(f"invalid ticker or role in universe: {fund.ticker!r} {fund.role!r}")
    return funds, hold_out


def _finite_positive(*values: object) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values)


def fetch_yahoo(symbol: str, opener: Opener, today: date) -> Series:
    url = YAHOO.format(symbol=urllib.parse.quote(symbol), end=int(datetime.now(UTC).timestamp()))
    try:
        result = json.loads(opener(url))["chart"]["result"][0]
        currency = CURRENCIES[result["meta"]["currency"]]
        quote, adjusted = result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]["adjclose"]
        rows = list(zip(result["timestamp"], quote["open"], quote["close"], adjusted, strict=True))
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ExportError(f"{symbol}: unexpected Yahoo response ({error!r})") from error
    by_day: dict[date, tuple[Bar, float]] = {}
    skipped = 0
    for stamp, open_, close, adj in rows:
        try:
            day = datetime.fromtimestamp(stamp, LONDON).date()
        except (TypeError, ValueError, OverflowError, OSError) as error:
            raise ExportError(f"{symbol}: bad timestamp {stamp!r}") from error
        if not _finite_positive(open_, close, adj) or day >= today:
            skipped += day < today  # today's partial bar isn't a fault
            continue
        factor = adj / close
        by_day[day] = (Bar(day, open_ * factor, adj), factor)  # a repeated day: the last row wins
    if not by_day:
        raise ExportError(f"{symbol}: no usable bars")
    days = sorted(by_day)
    bars = [by_day[d][0] for d in days]
    _check_bars(symbol, bars)
    years = (days[-1] - days[0]).days / 365.25
    change = by_day[days[-1]][1] / by_day[days[0]][1]
    try:
        yield_ = change ** (1 / years) - 1 if years > 0 else 0.0
    except OverflowError as error:
        raise ExportError(f"{symbol}: adjustment factor out of range") from error
    year_end = {d.year: by_day[d][1] for d in days}  # days are sorted: the last day of each year wins
    yearly = {y: year_end[y] / year_end[y - 1] - 1 for y in sorted(year_end) if y - 1 in year_end}
    return Series(symbol, currency, bars, skipped, yield_, yearly)


def _check_bars(symbol: str, bars: list[Bar]) -> None:
    for bar in bars:
        if abs(bar.open / bar.close - 1) > MAX_OPEN_CLOSE_GAP:
            raise ExportError(f"{symbol} {bar.day}: open {bar.open:.4f} far from close {bar.close:.4f}")
    for before, after in itertools.pairwise(bars):
        ratio = after.close / before.close
        if ratio >= MAX_DAILY_RATIO or ratio <= 1 / MAX_DAILY_RATIO:
            raise ExportError(f"{symbol} {after.day}: implausible jump of {ratio:.2f}x")


def yield_problem(source: Source, series: Series) -> str | None:
    low, high = DISTRIBUTING_YIELD
    if not source.distributing and series.yield_ > ACCUMULATING_MAX_YIELD:
        return f"{series.symbol}: marked accumulating but its adjustment implies a {series.yield_:.2%} yearly yield"
    if source.distributing and not low <= series.yield_ <= high:
        return f"{series.symbol}: marked distributing but its adjustment implies a {series.yield_:.2%} yearly yield"
    for year, value in series.yearly.items():
        if source.distributing and value < low:
            return f"{series.symbol}: marked distributing but shows no dividends in {year}"
        if not source.distributing and value > ACCUMULATING_MAX_YIELD:
            return (
                f"{series.symbol}: marked accumulating but shows a {value:.2%} dividend adjustment in {year}"
            )
    return None


def fetch_stooq(symbol: str, opener: Opener) -> dict[date, float]:
    text = opener(STOOQ.format(symbol=urllib.parse.quote(symbol))).decode(errors="replace")
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
        closes = {date.fromisoformat(r["Date"]): float(r["Close"]) for r in rows if r.get("Close")}
    except (KeyError, ValueError) as error:
        raise ExportError(f"{symbol}: unexpected Stooq response ({error!r})") from error
    closes = {d: c for d, c in closes.items() if _finite_positive(c)}
    if not closes:
        raise ExportError(f"{symbol}: no Stooq data ({text[:60]!r})")
    return closes


def _in_currency(series: Series, currency: str) -> list[Bar]:
    if series.currency == currency:
        return series.bars
    factor = {("GBP", "GBX"): 100.0, ("GBX", "GBP"): 0.01}.get((series.currency, currency))
    if factor is None:
        raise ExportError(f"{series.symbol}: can't convert {series.currency} to {currency}")
    return [Bar(b.day, b.open * factor, b.close * factor) for b in series.bars]


def splice(primary: Series, proxies: list[Series]) -> tuple[list[Bar], list[dict[str, str]]]:
    """Proxies (oldest first) under the primary, newest first, each scaled at its first common day."""
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
    """Daily returns on common days: coverage, disagreeing days and the worst gap."""
    common = [b for b in bars if b.day in second]
    disagree = total = 0
    worst = 0.0
    for before, after in itertools.pairwise(common):
        gap = abs((after.close / before.close - 1) - (second[after.day] / second[before.day] - 1))
        total += 1
        disagree += gap > RETURN_TOLERANCE
        worst = max(worst, gap)
    coverage = total / (len(bars) - 1) if len(bars) > 1 else 0.0
    return {
        "common_days": total,
        "coverage": coverage,
        "disagreeing_days": disagree,
        "share": disagree / total if total else 1.0,
        "worst": worst,
    }


def check_problems(symbol: str, check: dict[str, float]) -> list[str]:
    problems = []
    if check["share"] > MAX_DISAGREEING_SHARE:
        problems.append(
            f"{symbol}: {check['disagreeing_days']:.0f} of {check['common_days']:.0f} days disagree with Stooq"
        )
    if check["worst"] > HARD_DAY_LIMIT:
        problems.append(f"{symbol}: a single day differs from Stooq by {check['worst']:.1%}")
    if check["coverage"] < MIN_COVERAGE:
        problems.append(f"{symbol}: Stooq covers only {check['coverage']:.0%} of its days")
    return problems


def _write(path: Path, bars: list[Bar], currency: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "open", "close", "currency"])
        for b in bars:
            writer.writerow([b.day.isoformat(), f"{b.open:.6f}", f"{b.close:.6f}", currency])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_symbols(funds: list[Fund], opener: Opener, today: date) -> list[str]:
    lines = []
    for fund in funds:
        for source in [*fund.proxies, fund.primary]:
            try:
                s = fetch_yahoo(source.yahoo, opener, today)
                lines.append(
                    f"{fund.ticker:5} {source.yahoo:10} {s.currency} {s.bars[0].day} .. {s.bars[-1].day}"
                    f" ({len(s.bars)} bars, {s.skipped} skipped, implied yield {s.yield_:.2%})"
                )
            except ExportError as error:
                lines.append(f"{fund.ticker:5} {source.yahoo:10} FAILED: {error}")
            try:
                closes = fetch_stooq(source.stooq, opener)
                lines.append(
                    f"{fund.ticker:5} {source.stooq:10} stooq {min(closes)} .. {max(closes)} ({len(closes)} days)"
                )
            except ExportError as error:
                lines.append(f"{fund.ticker:5} {source.stooq:10} FAILED: {error}")
    return lines


def _export_fund(
    fund: Fund, hold_out: date, folder: Path, opener: Opener, today: date
) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    checks = {}
    series = []
    for source in [*fund.proxies, fund.primary]:
        s = fetch_yahoo(source.yahoo, opener, today)
        check = compare(s.bars, fetch_stooq(source.stooq, opener))
        problems += check_problems(source.yahoo, check)
        if (problem := yield_problem(source, s)) is not None:
            problems.append(problem)
        if s.skipped > MAX_SKIPPED_SHARE * (len(s.bars) + s.skipped):
            problems.append(
                f"{source.yahoo}: {s.skipped} of {len(s.bars) + s.skipped} rows skipped as unusable"
            )
        checks[source.yahoo] = check | {
            "skipped_rows": s.skipped,
            "implied_yield": s.yield_,
            "yearly_yield": s.yearly,
        }
        series.append(s)
    primary = series[-1]
    bars, splices = splice(primary, series[:-1])
    dev = [b for b in bars if b.day < hold_out]
    held = [b for b in bars if b.day >= hold_out]
    info = {
        "role": fund.role,
        "currency": primary.currency,
        "sources": checks,
        "splices": splices,
        "first": bars[0].day.isoformat(),
        "last": bars[-1].day.isoformat(),
        "dev_rows": len(dev),
        "hold_out_rows": len(held),
        "dev_sha256": _write(folder / "dev" / f"{fund.ticker}.csv", dev, primary.currency),
        "hold_out_sha256": _write(folder / "holdout" / f"{fund.ticker}.csv", held, primary.currency),
    }
    return info, problems


def export(  # noqa: PLR0913 - the export's inputs
    funds: list[Fund],
    hold_out: date,
    out: Path,
    opener: Opener,
    today: date,
    *,
    accept_problems: bool = False,
) -> dict[str, Any]:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ExportError(f"{out} exists and is not empty: choose a new folder")
    out.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        manifest: dict[str, Any] = {
            "created": datetime.now(UTC).isoformat(),
            "hold_out_start": hold_out.isoformat(),
            "accept_problems": accept_problems,
            "funds": {},
            "problems": [],
        }
        for fund in funds:
            manifest["funds"][fund.ticker], problems = _export_fund(fund, hold_out, building, opener, today)
            manifest["problems"] += problems
        if manifest["problems"] and not accept_problems:
            raise ExportError("data problems; nothing written:\n  " + "\n  ".join(manifest["problems"]))
        (building / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if out.exists():
            out.rmdir()  # empty, checked above
        building.rename(out)
    finally:
        if building.exists():
            shutil.rmtree(building)  # only the folder this run created
    return manifest


def main(argv: list[str] | None = None, opener: Opener = default_opener) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--list", action="store_true", help="show each symbol's date range; write nothing")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "history-export",
        help="a new or empty folder outside the repo",
    )
    parser.add_argument(
        "--accept-problems", action="store_true", help="write despite data problems (recorded)"
    )
    args = parser.parse_args(argv)
    today = datetime.now(LONDON).date()
    try:
        funds, hold_out = load_universe()
        if args.list:
            print("\n".join(list_symbols(funds, opener, today)))
            return 0
        if args.out.resolve().is_relative_to(ROOT):
            raise ExportError(f"--out {args.out} is inside the repository: use a folder outside it")
        manifest = export(funds, hold_out, args.out, opener, today, accept_problems=args.accept_problems)
    except (ExportError, OSError) as error:
        print(f"export failed: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(manifest['funds'])} funds to {args.out}")
    print(f"upload with: gcloud storage cp -r {args.out} gs://<your-private-bucket>/history-{today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
