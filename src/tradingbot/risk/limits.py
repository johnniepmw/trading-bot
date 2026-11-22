"""Risk ceilings and day-to-day limits (ADR-0009).

The ceilings are constants in code. Raising one needs an ADR and the owner's
`/approve-risk`. Day-to-day `Limits` may only be tighter: a looser value raises, so
invalid config stops the bot at start-up. No leverage is not a number here: `review`
never lets buys spend more than available cash.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

HARD_STOP_DRAWDOWN = Decimal("0.20")  # no new buys at 20% below the high-water mark
MAX_POSITION_FRACTION = Decimal("0.30")  # no buy takes a holding above 30% of equity
MAX_DAILY_LOSS = Decimal("0.05")  # the daily-loss pause can't be set looser than 5%
MAX_CAPITAL_GBP = Decimal(1000)  # the most the owner's `operator` secret can put at risk (AGENTS.md)


@dataclass(frozen=True, slots=True)
class Limits:
    position_fraction: Decimal = Decimal("0.25")
    drawdown_halve: Decimal = Decimal("0.10")  # past this, the position cap halves
    drawdown_stop: Decimal = Decimal("0.20")
    daily_loss: Decimal = Decimal("0.02")

    def __post_init__(self) -> None:
        ceilings = {
            "position_fraction": MAX_POSITION_FRACTION,
            "drawdown_halve": HARD_STOP_DRAWDOWN,
            "drawdown_stop": HARD_STOP_DRAWDOWN,
            "daily_loss": MAX_DAILY_LOSS,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
            if not value.is_finite() or not Decimal(0) < value <= ceiling:
                raise ValueError(f"{name} must be above 0 and at most {ceiling}, got {value}")
        if self.drawdown_halve >= self.drawdown_stop:
            raise ValueError("drawdown_halve must be below drawdown_stop")
