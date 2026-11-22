"""The EODHD price adapter against recorded responses (Phase 2 step 3, AC10; ADR-0018).

The responses follow EODHD's documented end-of-day format. The owner's
`scripts/eodhd_check.py` run checks them against the live API before prod uses it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest

from tradingbot.app.eodhd import EodhdHistory, read_key
from tradingbot.app.storage import TransientStorageError
from tradingbot.data.prices import DataError, validate_history
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
KEY = "k3y-not-real"
ROWS = [
    {
        "date": "2026-11-18",
        "open": 100.0,
        "high": 101.0,
        "low": 99.5,
        "close": 100.5,
        "adjusted_close": 100.5,
        "volume": 1000,
    },
    {
        "date": "2026-11-19",
        "open": 100.5,
        "high": 102.0,
        "low": 100.0,
        "close": 101.0,
        "adjusted_close": 101.0,
        "volume": 1200,
    },
    {
        "date": "2026-11-20",
        "open": 101.0,
        "high": 101.5,
        "low": 100.2,
        "close": 100.8,
        "adjusted_close": 100.8,
        "volume": 900,
    },
]


class Recorder:
    def __init__(self, *responses: tuple[int, bytes]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.urls.append(url)
        return self.responses.pop(0)


def history(*responses: tuple[int, bytes]) -> tuple[EodhdHistory, Recorder]:
    recorder = Recorder(*responses)
    return EodhdHistory(recorder, KEY), recorder


def test_a_request_names_the_london_listing_and_the_dates() -> None:
    source, sent = history((200, json.dumps(ROWS).encode()))
    source.daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20))
    assert sent.urls == [
        "https://eodhd.com/api/eod/VWRP.LSE?from=2026-11-18&to=2026-11-20&period=d&fmt=json&api_token=k3y-not-real"
    ]


def test_bars_use_dividend_adjusted_closes_with_opens_scaled_alike() -> None:
    rows = [{**ROWS[0], "close": 100.0, "adjusted_close": 98.0, "open": 99.0}]
    source, _ = history((200, json.dumps(rows).encode()))
    [bar] = source.daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 18))
    assert (bar.day, bar.close, bar.open) == (date(2026, 11, 18), 98.0, pytest.approx(99.0 * 0.98))


def test_bars_pass_the_history_checks_in_date_order() -> None:
    source, _ = history((200, json.dumps(list(reversed(ROWS))).encode()))
    bars = validate_history(VWRP, source.daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20)))
    assert [b.day for b in bars] == [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]


def test_rows_outside_the_range_are_dropped() -> None:
    source, _ = history((200, json.dumps(ROWS).encode()))
    assert [b.day for b in source.daily_bars(VWRP, date(2026, 11, 19), date(2026, 11, 19))] == [
        date(2026, 11, 19)
    ]


def test_prices_stay_in_the_listings_units() -> None:
    # a pence-quoted fund comes back in pence: the instrument says GBX, and the cycle's
    # broker cross-check stops a run if the configured unit is ever wrong (a 100x gap)
    rows = [{**ROWS[0], "open": 4000.0, "close": 4010.0, "adjusted_close": 4010.0}]
    source, _ = history((200, json.dumps(rows).encode()))
    [bar] = source.daily_bars(SGLN, date(2026, 11, 18), date(2026, 11, 18))
    assert (bar.instrument.currency, bar.close) == (Currency.GBX, 4010.0)


@pytest.mark.parametrize(
    "response",
    [
        (401, b'{"error": "unauthenticated k3y-not-real"}'),
        (429, b""),
        (200, b"not json"),
        (200, b'{"error": "limit reached"}'),
        (200, json.dumps([{"date": "2026-11-18", "close": 1.0}]).encode()),  # no adjusted close
        (200, json.dumps([{**ROWS[0], "adjusted_close": "n/a"}]).encode()),
        (200, json.dumps([{**ROWS[0], "date": "18/11/2026"}]).encode()),
    ],
)
def test_bad_responses_are_data_errors_that_never_show_the_key(response: tuple[int, bytes]) -> None:
    source, _ = history(response)
    with pytest.raises(DataError) as raised:
        source.daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20))
    assert KEY not in str(raised.value)
    assert "VWRP" in str(raised.value)


def test_a_network_failure_is_retried_once() -> None:
    calls = 0

    def flaky(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientStorageError(f"GET {url.split('?', maxsplit=1)[0]}: timed out")
        return 200, json.dumps(ROWS).encode()

    assert len(EodhdHistory(flaky, KEY).daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20))) == 3


def test_a_lasting_network_failure_is_a_data_error_without_the_key() -> None:
    def down(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
        raise TransientStorageError(f"GET {url.split('?', maxsplit=1)[0]}: connection refused")

    with pytest.raises(DataError) as raised:
        EodhdHistory(down, KEY).daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20))
    assert KEY not in str(raised.value)


def test_an_empty_answer_is_no_bars() -> None:
    source, _ = history((200, b"[]"))
    assert (
        source.daily_bars(VWRP, date(2026, 11, 18), date(2026, 11, 20)) == []
    )  # the cycle's stale-data check stops


# --- the key ------------------------------------------------------------------------------
def test_the_key_is_read_from_its_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "value"
    secret.write_text("abc123\n")
    assert read_key(secret) == "abc123"


@pytest.mark.parametrize("content", [None, "", "__unset__", "two words"])
def test_a_missing_or_placeholder_key_is_refused(tmp_path: Path, content: str | None) -> None:
    secret = tmp_path / "value"
    if content is not None:
        secret.write_text(content)
    with pytest.raises(DataError, match="EODHD key"):
        read_key(secret)
