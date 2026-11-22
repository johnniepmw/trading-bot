"""End-of-day prices from EODHD (Phase 2 step 3; ADR-0018), behind `PriceHistory`.

One request per fund per run: `/api/eod/<TICKER>.LSE` with a date range, as JSON. Bars use
the dividend-adjusted close (`adjusted_close`), with the open scaled by the same factor, so
signals follow total return as in the backtest. Prices stay in the listing's units (pence
for GBX funds), matching the instrument's currency; the cycle's cross-check against the
broker's live price stops a run if a configured unit is ever wrong.

The API key travels in the query string (EODHD's scheme), so no URL, response body or
transport error text ever reaches an exception or a log line. The transport refuses
redirects and ignores proxy settings (`app/storage.py`).
"""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tradingbot.app.storage import StorageError, Transport
from tradingbot.data.prices import DataError
from tradingbot.domain.market import Bar, Instrument

BASE = "https://eodhd.com/api/eod/"
KEY_PATH = Path("/secrets/eodhd-key/value")  # mounted in prod only (infra/envs/prod)
EXCHANGE = "LSE"
ATTEMPTS = 2


def read_key(path: Path | None = None) -> str:
    try:
        key = (path or KEY_PATH).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise DataError(f"EODHD key unreadable ({type(error).__name__})") from error
    if not key or key == "__unset__" or not key.isprintable() or " " in key:
        raise DataError("EODHD key missing or not set (make secret-set NAME=eodhd-key ENV=prod)")
    return key


def _number(row: dict[str, object], field: str) -> float:
    value = row.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(field)
    return float(value)


class EodhdHistory:
    def __init__(self, transport: Transport, key: str) -> None:
        self._send = transport
        self._key = key

    def _get(self, ticker: str, start: date, end: date) -> bytes:
        query = urllib.parse.urlencode(
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "period": "d",
                "fmt": "json",
                "api_token": self._key,
            }
        )
        url = f"{BASE}{urllib.parse.quote(ticker, safe='')}.{EXCHANGE}?{query}"
        for attempt in range(ATTEMPTS):
            try:
                status, body = self._send("GET", url, {}, None)
            except StorageError as error:  # network failure; its text names no query string, but stay safe
                if attempt == ATTEMPTS - 1:
                    raise DataError(f"EODHD {ticker}: network error ({type(error).__name__})") from None
                continue
            if status != 200:  # noqa: PLR2004
                raise DataError(f"EODHD {ticker}: HTTP {status}")
            return body
        raise AssertionError("unreachable")  # pragma: no cover

    def daily_bars(self, instrument: Instrument, start: date, end: date) -> Sequence[Bar]:
        ticker = instrument.ticker
        body = self._get(ticker, start, end)
        try:
            rows = json.loads(body)
            if not isinstance(rows, list):
                raise ValueError("not a list")
            bars = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("row")
                day = date.fromisoformat(str(row.get("date")))
                if not start <= day <= end:
                    continue
                close, adjusted, opened = (
                    _number(row, "close"),
                    _number(row, "adjusted_close"),
                    _number(row, "open"),
                )
                bars.append(Bar(instrument, day, open=opened * adjusted / close, close=adjusted))
        except (ValueError, TypeError) as error:
            raise DataError(f"EODHD {ticker}: unexpected response ({type(error).__name__})") from None
        return sorted(bars, key=lambda b: b.day)
