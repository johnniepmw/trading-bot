"""The owner's EODHD check script, on recorded data (no network)."""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from tradingbot.data.prices import DataError
from tradingbot.domain.market import Bar, Instrument
from tradingbot.domain.money import Currency

SPEC = importlib.util.spec_from_file_location(
    "eodhd_check", Path(__file__).resolve().parents[2] / "scripts" / "eodhd_check.py"
)
assert SPEC is not None and SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

TODAY = date(2026, 11, 23)
VWRP, SGLN, IGLT, EMIM = (
    Instrument("VWRP", Currency.GBP),
    Instrument("SGLN", Currency.GBX),
    Instrument("IGLT", Currency.GBX),
    Instrument("EMIM", Currency.GBX),
)


class Recorded:
    def daily_bars(self, instrument: Instrument, start: date, end: date) -> Sequence[Bar]:
        if instrument is EMIM:
            raise DataError("EODHD EMIM: HTTP 404")
        if instrument is IGLT:
            return []
        price = {VWRP: 120.5, SGLN: 120.5}[instrument]  # SGLN looks like pounds: a wrong unit
        return [Bar(instrument, end - timedelta(days=n), open=price, close=price) for n in (2, 1, 0)][::1]


def test_the_report_shows_coverage_units_and_problems() -> None:
    lines = check.report(Recorded(), [VWRP, SGLN, IGLT, EMIM], TODAY)
    assert lines[1].startswith("VWRP       3") and lines[1].endswith("GBP / GBP")
    assert lines[2].endswith("GBX / GBP  <-- check")
    assert lines[3] == "IGLT   no bars"
    assert lines[4] == "EMIM   error: EODHD EMIM: HTTP 404"
