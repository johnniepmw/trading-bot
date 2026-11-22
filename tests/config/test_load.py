"""The bot's config file: typed, validated at start-up, baked into the image (Phase 2 AC4, AC5)."""

from __future__ import annotations

import tomllib
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.config.load import BUNDLED, ConfigFileError, load_config
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency

ROOT = Path(__file__).resolve().parents[2]
GOOD = """
instruments_verified = false
schedule_time = "07:15"
operator_secret_path = "/secrets/prod-operator/value"

[limits]
position_fraction = "0.25"
drawdown_halve = "0.10"
drawdown_stop = "0.20"
daily_loss = "0.02"

[calendar]
until = "2026-12-31"
holidays = ["2026-12-25", "2026-12-28"]

[[instrument]]
ticker = "VWRP"
currency = "GBP"
quantity_step = "0.0001"

[[instrument]]
ticker = "SGLN"
currency = "GBX"
quantity_step = "0.001"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bot.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_valid_file_loads_into_typed_values(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    assert config.universe == {
        Instrument("VWRP", Currency.GBP): Decimal("0.0001"),
        Instrument("SGLN", Currency.GBX): Decimal("0.001"),
    }
    assert config.limits.drawdown_stop == Decimal("0.20")
    assert config.schedule_time == time(7, 15)
    assert config.holidays == frozenset({date(2026, 12, 25), date(2026, 12, 28)})
    assert config.calendar_until == date(2026, 12, 31)
    assert config.operator_secret_path == "/secrets/prod-operator/value"  # noqa: S105 - a path
    assert config.instruments_verified is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('daily_loss = "0.02"', 'daily_loss = "0.06"'), "daily_loss"),  # looser than the ceiling
        (('daily_loss = "0.02"', "daily_loss = 0.02"), "daily_loss"),  # a float, not a decimal string
        (('currency = "GBX"', 'currency = "EUR"'), "currency"),
        (('quantity_step = "0.001"', 'quantity_step = "0"'), "quantity_step"),
        (('schedule_time = "07:15"', 'schedule_time = "7am"'), "schedule_time"),
        (('until = "2026-12-31"', 'until = "soon"'), "until"),
        (('"2026-12-28"]', '"2026-12-26"]'), "holiday"),  # a Saturday listed as a holiday: a typo
        (("instruments_verified = false", 'instruments_verified = "no"'), "instruments_verified"),
        (('ticker = "SGLN"', 'ticker = "VWRP"'), "twice"),
        (('ticker = "SGLN"', 'ticker = "SSAC"'), "benchmark"),
        (("[limits]", "colour = 1\n[limits]"), "unknown"),  # a misspelt key is an error, not ignored
        (('operator_secret_path = "/secrets/prod-operator/value"', ""), "operator_secret_path"),
        (('ticker = "SGLN"', 'ticker = "vwrp"'), "twice"),  # tickers compare without case
        (('schedule_time = "07:15"', 'schedule_time = "07:15+01:00"'), "schedule_time"),  # London wall-clock
        (('schedule_time = "07:15"', 'schedule_time = "07:15:30"'), "schedule_time"),
        (('until = "2026-12-31"', 'until = "2026-12-30"'), "until"),  # must end a year
        (('until = "2026-12-31"', 'until = "2027-12-31"'), "2027"),  # a year with no holidays listed
        (('"2026-12-28"]', '"2026-12-28", "2027-01-04"]'), "holiday"),  # after `until`
        (('daily_loss = "0.02"\n', ""), "daily_loss"),  # missing
        (('drawdown_halve = "0.10"', 'drawdown_halve = "0.20"'), "drawdown_halve"),
        (('quantity_step = "0.001"', 'quantity_step = "NaN"'), "quantity_step"),
        (('quantity_step = "0.001"', 'quantity_step = "Infinity"'), "quantity_step"),
        (('ticker = "SGLN"', 'ticker = "SG.LN"'), "letters"),
        (('holidays = ["2026-12-25", "2026-12-28"]', 'holidays = "2026-12-25"'), "list"),
    ],
)
def test_invalid_config_is_refused(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    old, new = change
    assert old in GOOD
    with pytest.raises(ConfigFileError, match=message):
        load_config(write(tmp_path, GOOD.replace(old, new, 1)))


def test_an_empty_universe_is_refused(tmp_path: Path) -> None:
    text = GOOD.split("[[instrument]]", maxsplit=1)[0]
    with pytest.raises(ConfigFileError, match="instrument"):
        load_config(write(tmp_path, text))


def test_malformed_toml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r"bot\.toml"):
        load_config(write(tmp_path, "schedule_time = "))


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match="missing"):
        load_config(tmp_path / "nope.toml")


def test_the_bundled_file_loads() -> None:
    config = load_config(BUNDLED)
    assert config.operator_secret_path == "/secrets/prod-operator/value"  # noqa: S105 - a path


def test_the_bundled_universe_is_the_strategys_trade_funds() -> None:
    research = tomllib.loads((ROOT / "research" / "universe.toml").read_text(encoding="utf-8"))
    traded = sorted(f["ticker"] for f in research["fund"] if f["role"] == "trade")
    assert sorted(i.ticker for i in load_config(BUNDLED).universe) == traded


def test_the_bundled_calendar_covers_the_next_year() -> None:
    assert load_config(BUNDLED).calendar_until >= date(2027, 12, 31)


def test_the_bundled_calendar_lists_englands_eight_closures_each_year() -> None:
    holidays = load_config(BUNDLED).holidays
    assert sorted({d.year for d in holidays}) == [2026, 2027]
    assert [sum(d.year == y for d in holidays) for y in (2026, 2027)] == [8, 8]


def test_the_bundled_units_match_the_data_sources_quotes() -> None:
    # scripts/eodhd_check.py, 2026-11-22: IGLT 9.47 and INXG 10.46 are pounds; EMIM and SGLN pence
    units = {i.ticker: i.currency.value for i in load_config(BUNDLED).universe}
    assert units == {
        "VWRP": "GBP",
        "VUKE": "GBP",
        "EMIM": "GBX",
        "IGLT": "GBP",
        "VAGP": "GBP",
        "INXG": "GBP",
        "SGLN": "GBX",
    }
