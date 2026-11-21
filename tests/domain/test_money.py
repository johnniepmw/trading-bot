from __future__ import annotations

import operator
from collections.abc import Callable
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradingbot.domain.money import Currency, CurrencyMismatchError, Money

amounts = st.decimals(min_value=-(10**9), max_value=10**9, allow_nan=False, allow_infinity=False, places=4)


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


def gbx(x: str) -> Money:
    return Money(Decimal(x), Currency.GBX)


def test_same_currency_arithmetic() -> None:
    assert gbp("1.10") + gbp("2.20") == gbp("3.30")
    assert gbp("5") - gbp("7.5") == gbp("-2.5")
    assert -gbp("3") == gbp("-3")
    assert gbp("2.5") * Decimal(4) == gbp("10")
    assert gbp("1") < gbp("2") <= gbp("2")
    assert gbp("3") > gbp("2") >= gbp("2")


@pytest.mark.parametrize(
    "op", [operator.add, operator.sub, operator.lt, operator.le, operator.gt, operator.ge]
)
def test_refuses_to_mix_pounds_and_pence(op: Callable[[Money, Money], object]) -> None:
    with pytest.raises(CurrencyMismatchError, match=r"GBP.*GBX"):
        op(gbp("1"), gbx("100"))


def test_pounds_and_pence_are_never_equal() -> None:
    assert gbp("1") != gbx("100")


def test_pence_convert_to_pounds_exactly() -> None:
    assert gbx("12345.6").to_gbp() == gbp("123.456")
    assert gbp("7").to_gbp() == gbp("7")


def test_other_currencies_do_not_convert_implicitly() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(Decimal(1), Currency.USD).to_gbp()


@pytest.mark.parametrize("bad", [1.5, 2, "3"])
def test_amount_must_be_decimal(bad: object) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(bad, Currency.GBP)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_amount_must_be_finite(bad: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        Money(Decimal(bad), Currency.GBP)


def test_scalar_must_be_decimal() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        gbp("1") * 1.5  # type: ignore[operator]


@given(amounts, amounts)
def test_addition_commutes_and_subtraction_inverts(a: Decimal, b: Decimal) -> None:
    x, y = Money(a, Currency.GBP), Money(b, Currency.GBP)
    assert x + y == y + x
    assert (x + y) - y == x


@given(amounts)
def test_pence_to_pounds_is_exact_division_by_100(a: Decimal) -> None:
    assert Money(a * 100, Currency.GBX).to_gbp() == Money(a, Currency.GBP)
