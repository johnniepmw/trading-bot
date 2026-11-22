"""Load and validate the bot's config file (Phase 2 AC4). Invalid config: the bot refuses to start.

The file is baked into the image (`bot.toml` next to this module) and is a high-risk path.
Per-environment wiring (APP_ENV, buckets, the broker's base URL) comes from env vars in
`app`, never from here; secrets are files.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from tradingbot.config.types import BotConfig, LimitsConfig
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency
from tradingbot.risk.limits import Limits

BUNDLED = Path(__file__).with_name("bot.toml")
BENCHMARK_ONLY = frozenset({"SSAC"})  # monitoring spec: compared against, never traded
_TOP = {"instruments_verified", "schedule_time", "operator_secret_path", "limits", "calendar", "instrument"}


class ConfigFileError(ValueError):
    """The config file is missing or invalid."""


def _keys(table: Mapping[str, object], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigFileError(f"{where}: unknown key(s) {', '.join(unknown)}")
    missing = sorted(allowed - set(table))
    if missing:
        raise ConfigFileError(f"{where}: missing {', '.join(missing)}")


def _parse[T](value: object, name: str, parse: Callable[[str], T]) -> T:
    if not isinstance(value, str):
        raise ConfigFileError(f"{name} must be a quoted string, got {type(value).__name__}")
    try:
        return parse(value)
    except (ValueError, InvalidOperation) as error:
        raise ConfigFileError(f"{name}: {value!r} is invalid") from error


def _positive(value: object, name: str) -> Decimal:
    amount = _parse(value, name, Decimal)
    if not amount.is_finite() or amount <= 0:
        raise ConfigFileError(f"{name} must be a positive number, got {value!r}")
    return amount


def _limits(raw: object) -> LimitsConfig:
    if not isinstance(raw, dict):
        raise ConfigFileError("[limits] must be a table")
    _keys(raw, {"position_fraction", "drawdown_halve", "drawdown_stop", "daily_loss"}, "[limits]")
    config = LimitsConfig(**{k: _parse(v, k, Decimal) for k, v in raw.items()})
    try:
        Limits(**vars(config))  # the risk layer's ceilings decide what is allowed
    except (ValueError, TypeError) as error:
        raise ConfigFileError(f"[limits]: {error}") from error
    return config


def _universe(raw: object) -> Mapping[Instrument, Decimal]:
    if not isinstance(raw, list) or not raw:
        raise ConfigFileError("[[instrument]] must list at least one instrument")
    universe: dict[Instrument, Decimal] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigFileError("each [[instrument]] must be a table")
        _keys(entry, {"ticker", "currency", "quantity_step"}, "[[instrument]]")
        ticker = entry["ticker"]
        if not isinstance(ticker, str) or not ticker.isalnum():
            raise ConfigFileError(f"ticker must be letters and digits, got {ticker!r}")
        if ticker.upper() in BENCHMARK_ONLY:
            raise ConfigFileError(f"{ticker} is a benchmark only and can't be in the trading universe")
        if any(i.ticker.upper() == ticker.upper() for i in universe):
            raise ConfigFileError(f"{ticker} is listed twice")
        currency = _parse(entry["currency"], f"{ticker} currency", Currency)
        universe[Instrument(ticker, currency)] = _positive(entry["quantity_step"], f"{ticker} quantity_step")
    return MappingProxyType(universe)


def _calendar(raw: object) -> tuple[frozenset[date], date]:
    if not isinstance(raw, dict):
        raise ConfigFileError("[calendar] must be a table")
    _keys(raw, {"until", "holidays"}, "[calendar]")
    until = _parse(raw["until"], "calendar until", date.fromisoformat)
    if (until.month, until.day) != (12, 31):
        raise ConfigFileError(f"calendar until must be 31 December, got {until}")
    holidays = raw["holidays"]
    if not isinstance(holidays, list):
        raise ConfigFileError("calendar holidays must be a list of dates")
    days = frozenset(_parse(h, "calendar holiday", date.fromisoformat) for h in holidays)
    for day in sorted(days):
        if day.weekday() >= 5 or day > until:  # noqa: PLR2004 - Saturday
            raise ConfigFileError(f"calendar holiday {day} is a weekend day or after `until`: a typo?")
    missing = [
        y
        for y in range(min(d.year for d in days or {until}), until.year + 1)
        if not any(d.year == y for d in days)
    ]
    if missing:
        raise ConfigFileError(
            f"calendar lists no holidays for {', '.join(map(str, missing))}: add them before `until`"
        )
    return days, until


def _wall_clock(text: str) -> time:
    """HH:MM, London wall-clock time: no seconds, no offset."""
    parsed = time.fromisoformat(text)
    if len(text) != 5 or parsed.tzinfo is not None:  # noqa: PLR2004 - "HH:MM"
        raise ValueError("expected HH:MM")
    return parsed


def load_config(path: Path = BUNDLED) -> BotConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigFileError(f"config file missing: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigFileError(f"{path.name}: {error}") from error
    _keys(raw, _TOP, path.name)
    verified = raw["instruments_verified"]
    if not isinstance(verified, bool):
        raise ConfigFileError("instruments_verified must be true or false")
    secret_path = raw["operator_secret_path"]
    if not isinstance(secret_path, str) or not secret_path.startswith("/secrets/"):
        raise ConfigFileError("operator_secret_path must be a path under /secrets/")
    holidays, until = _calendar(raw["calendar"])
    return BotConfig(
        universe=_universe(raw["instrument"]),
        limits=_limits(raw["limits"]),
        schedule_time=_parse(raw["schedule_time"], "schedule_time", _wall_clock),
        holidays=holidays,
        calendar_until=until,
        operator_secret_path=secret_path,
        instruments_verified=verified,
    )
