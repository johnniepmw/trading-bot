"""The owner's `operator` secret: the live switch and capital (ADR-0009; Phase 2 AC8).

Read as a file. Missing, unreadable, the `__unset__` placeholder or anything invalid means
paper trading; an invalid value is logged (never its content) so the owner hears of it.
The format is JSON: {"mode": "paper"} or {"mode": "live", "capital_gbp": "<amount>"}.
Parsing a valid live setting doesn't make anything live: in Phase 2 no live broker can be
built at all (the broker factory, step 2b).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

log = logging.getLogger(__name__)

PLACEHOLDER = "__unset__"
MAX_CAPITAL_GBP = Decimal(1000)  # the project's ceiling (AGENTS.md: up to £1,000)


@dataclass(frozen=True)
class Operator:
    live: bool
    capital_gbp: Decimal | None


PAPER = Operator(live=False, capital_gbp=None)


def _parse(text: str) -> Operator:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    if raw == {"mode": "paper"}:
        return PAPER
    if (
        set(raw) != {"mode", "capital_gbp"}
        or raw["mode"] != "live"
        or not isinstance(raw["capital_gbp"], str)
    ):
        raise ValueError("expected mode and capital_gbp")
    capital = Decimal(raw["capital_gbp"])
    if not capital.is_finite() or not Decimal(0) < capital <= MAX_CAPITAL_GBP:
        raise ValueError("capital out of range")
    return Operator(live=True, capital_gbp=capital)


def read_operator(path: Path) -> Operator:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return PAPER
    except OSError as error:
        log.error("operator secret unreadable (%s): paper trading", type(error).__name__)
        return PAPER
    if text == PLACEHOLDER:
        return PAPER
    try:
        return _parse(text)
    except (ValueError, InvalidOperation):  # json.JSONDecodeError is a ValueError
        log.error("operator secret invalid: paper trading (the owner sets it with make secret-set)")
        return PAPER
