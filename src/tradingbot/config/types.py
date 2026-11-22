"""Config types (no I/O: the pure core may import this module; ARCHITECTURE.md contract 2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from tradingbot.domain.market import Instrument


@dataclass(frozen=True)
class LimitsConfig:
    """The risk layer's day-to-day limits; `risk.Limits` checks them against its ceilings."""

    position_fraction: Decimal
    drawdown_halve: Decimal
    drawdown_stop: Decimal
    daily_loss: Decimal


@dataclass(frozen=True)
class BotConfig:
    universe: Mapping[Instrument, Decimal]  # traded instruments and their quantity steps
    limits: LimitsConfig
    schedule_time: time  # weekdays, Europe/London (the scheduler's time; the deploy window uses it)
    holidays: frozenset[date]  # London Stock Exchange closures on weekdays
    calendar_until: date  # the last day the holiday list covers
    operator_secret_path: str  # fixed here, never from an env var (step 0.5 security review)
    instruments_verified: bool  # currencies and quantity steps checked against the broker
