from __future__ import annotations

from decimal import Decimal

import pytest

from tradingbot.risk.limits import (
    HARD_STOP_DRAWDOWN,
    MAX_DAILY_LOSS,
    MAX_POSITION_FRACTION,
    Limits,
)


def test_ceilings_are_the_adr_0009_values() -> None:
    assert Decimal("0.20") == HARD_STOP_DRAWDOWN
    assert Decimal("0.30") == MAX_POSITION_FRACTION
    assert Decimal("0.05") == MAX_DAILY_LOSS


def test_default_limits_sit_inside_the_ceilings_and_fit_the_strategy() -> None:
    limits = Limits()
    assert limits.position_fraction == Decimal("0.25")  # the strategy's 25% sleeve cap must fit
    assert limits.drawdown_halve == Decimal("0.10")
    assert limits.drawdown_stop == Decimal("0.20")
    assert limits.daily_loss == Decimal("0.02")


def test_limits_may_be_tighter() -> None:
    tight = Limits(
        position_fraction=Decimal("0.10"),
        drawdown_halve=Decimal("0.05"),
        drawdown_stop=Decimal("0.08"),
        daily_loss=Decimal("0.01"),
    )
    assert tight.drawdown_stop == Decimal("0.08")


@pytest.mark.parametrize(
    "field,value",
    [
        ("position_fraction", Decimal("0.31")),  # above the ceiling
        ("drawdown_stop", Decimal("0.21")),
        ("daily_loss", Decimal("0.051")),
        ("drawdown_halve", Decimal("0.20")),  # must be below the stop
        ("position_fraction", Decimal(0)),
        ("daily_loss", Decimal("-0.01")),
        ("drawdown_halve", Decimal(0)),
        ("position_fraction", Decimal("NaN")),
    ],
)
def test_limits_can_never_be_looser_than_the_ceilings(field: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match=field):
        Limits(**{field: value})


def test_limits_must_be_decimals() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Limits(daily_loss=0.02)  # type: ignore[arg-type]
