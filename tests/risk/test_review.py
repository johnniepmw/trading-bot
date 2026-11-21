from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import pytest

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, CurrencyMismatchError, Money
from tradingbot.risk.review import AccountState, Decision, review

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)  # quoted in pence
SPY = Instrument("SPY", Currency.USD)
STEP = Decimal("0.01")


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def state(  # noqa: PLR0913 - a test builder with defaults
    *,
    equity: str = "1000",
    cash: str = "1000",
    hwm: str | None = "1000",
    day_start: str | None = None,
    holdings: Mapping[Instrument, Decimal] | None = None,
    quotes: Mapping[Instrument, Money] | None = None,
    steps: Mapping[Instrument, Decimal] | None = None,
) -> AccountState:
    return AccountState(
        equity=gbp(equity),
        cash=gbp(cash),
        high_water_mark=gbp(hwm) if hwm is not None else None,
        day_start_equity=gbp(day_start if day_start is not None else equity),
        holdings=holdings or {},
        quotes=quotes if quotes is not None else {VWRP: gbp(100), SGLN: Money(Decimal(4000), Currency.GBX)},
        quantity_steps=steps if steps is not None else {VWRP: STEP, SGLN: STEP, SPY: STEP},
    )


def buy(qty: str, inst: Instrument = VWRP) -> Order:
    return Order(inst, Side.BUY, Decimal(qty))


def sell(qty: str, inst: Instrument = VWRP) -> Order:
    return Order(inst, Side.SELL, Decimal(qty))


def approved_qty(decision: Decision) -> Decimal | None:
    return decision.approved.order.quantity if decision.approved else None


# --- buys within limits pass unchanged ---------------------------------------------
def test_a_buy_within_every_limit_passes_unchanged() -> None:
    [d] = review([buy("2")], state())
    assert approved_qty(d) == Decimal(2)
    assert d.approved is not None and d.approved.order == buy("2")
    assert d.reason == "passed"


# --- position cap ------------------------------------------------------------------
def test_a_buy_is_shrunk_to_the_position_cap() -> None:
    # 25% of £1,000 = £250 = 2.5 units at £100
    [d] = review([buy("4")], state())
    assert approved_qty(d) == Decimal("2.5")
    assert d.reason.startswith("shrunk")


def test_existing_holdings_count_towards_the_cap() -> None:
    [d] = review([buy("4")], state(holdings={VWRP: Decimal(2)}))
    assert approved_qty(d) == Decimal("0.5")


def test_a_buy_at_or_over_the_cap_is_rejected() -> None:
    [d] = review([buy("1")], state(holdings={VWRP: Decimal("2.5")}))
    assert d.approved is None
    assert d.reason == "rejected: position cap"


def test_two_buys_of_one_instrument_share_the_cap() -> None:
    first, second = review([buy("2"), buy("2")], state())
    assert approved_qty(first) == Decimal(2)
    assert approved_qty(second) == Decimal("0.5")


def test_pence_quotes_are_converted_to_pounds_not_mixed() -> None:
    # 4000p = £40, so £250 buys 6.25 units
    [d] = review([buy("10", SGLN)], state())
    assert approved_qty(d) == Decimal("6.25")


def test_an_off_step_quantity_is_rounded_down_and_says_why() -> None:
    [d] = review([buy("1.005")], state())
    assert approved_qty(d) == Decimal("1.00")
    assert d.reason == "shrunk: to the quantity step"


def test_a_quantity_below_one_step_is_rejected() -> None:
    [d] = review([buy("0.004")], state())
    assert d.reason == "rejected: below the quantity step"


def test_shrink_reasons_name_the_binding_limit() -> None:
    [cap] = review([buy("4")], state())
    [cash] = review([buy("2")], state(cash="150"))
    assert (cap.reason, cash.reason) == ("shrunk: to the position cap", "shrunk: to available cash")


def test_shrunk_quantities_round_down_to_the_step() -> None:
    # £250 at £30 = 8.333.. units -> 8.33
    [d] = review([buy("20")], state(quotes={VWRP: gbp(30)}))
    assert approved_qty(d) == Decimal("8.33")


# --- no leverage: cash -------------------------------------------------------------
def test_buys_never_spend_more_than_available_cash() -> None:
    [d] = review([buy("2")], state(cash="150"))
    assert approved_qty(d) == Decimal("1.5")


def test_cash_is_shared_across_buys_in_order() -> None:
    a, b = review([buy("1.5"), buy("4", SGLN)], state(cash="200"))
    assert approved_qty(a) == Decimal("1.5")  # £150
    assert approved_qty(b) == Decimal("1.25")  # £50 left / £40
    _, c = review([buy("2"), buy("1", SGLN)], state(cash="200"))
    assert c.reason == "rejected: no cash"


def test_sale_proceeds_do_not_fund_buys_in_the_same_run() -> None:
    _, d = review([sell("1", SGLN), buy("1")], state(cash="0", holdings={SGLN: Decimal(5)}))
    assert d.reason == "rejected: no cash"


# --- drawdown ----------------------------------------------------------------------
def test_buys_are_vetoed_without_a_high_water_mark() -> None:
    [d] = review([buy("1")], state(hwm=None))
    assert d.reason == "rejected: no high-water mark"


def test_buys_are_vetoed_at_the_drawdown_stop() -> None:
    [d] = review([buy("1")], state(equity="800", cash="800", hwm="1000"))
    assert d.reason == "rejected: drawdown stop"


def test_past_the_first_drawdown_tier_the_cap_halves() -> None:
    # 10% down: cap is 12.5% of £900 = £112.50 = 1.125 -> 1.12 units
    [d] = review([buy("2")], state(equity="900", cash="900", hwm="1000"))
    assert approved_qty(d) == Decimal("1.12")


def test_equity_above_the_mark_is_not_a_drawdown() -> None:
    [d] = review([buy("2")], state(equity="1100", cash="1100", hwm="1000"))
    assert approved_qty(d) == Decimal(2)


# --- daily loss --------------------------------------------------------------------
def test_buys_stop_after_the_daily_loss_limit() -> None:
    [d] = review([buy("1")], state(equity="980", cash="980", hwm="1000", day_start="1000"))
    assert d.reason == "rejected: daily loss limit"


def test_a_smaller_daily_loss_allows_buys() -> None:
    [d] = review([buy("1")], state(equity="985", cash="985", hwm="1000", day_start="1000"))
    assert d.approved is not None


# --- missing inputs fail closed ----------------------------------------------------
def test_a_buy_without_a_quote_is_rejected() -> None:
    [d] = review([buy("1")], state(quotes={}))
    assert d.reason == "rejected: no quote"


def test_a_buy_without_a_quantity_step_is_rejected() -> None:
    [d] = review([buy("1")], state(steps={}))
    assert d.reason == "rejected: no quantity step"


def test_a_buy_quoted_in_a_currency_without_a_rate_is_rejected() -> None:
    [d] = review([buy("1", SPY)], state(quotes={SPY: Money(Decimal(500), Currency.USD)}))
    assert d.reason == "rejected: no GBP price"


# --- sells -------------------------------------------------------------------------
def test_sells_go_first_and_are_never_blocked_by_drawdown_or_loss_limits() -> None:
    decisions = review(
        [buy("1"), sell("1")],
        state(equity="700", cash="700", hwm=None, day_start="1000", holdings={VWRP: Decimal(3)}),
    )
    assert [d.order.side for d in decisions] == [Side.SELL, Side.BUY]
    assert approved_qty(decisions[0]) == Decimal(1)
    assert decisions[1].approved is None


def test_a_sell_is_shrunk_to_the_holding_no_shorting() -> None:
    [d] = review([sell("5")], state(holdings={VWRP: Decimal(2)}))
    assert approved_qty(d) == Decimal(2)
    assert d.reason.startswith("shrunk")


def test_sells_in_one_run_share_the_holding() -> None:
    a, b, c = review([sell("1"), sell("1"), sell("1")], state(holdings={VWRP: Decimal("1.5")}))
    assert [approved_qty(a), approved_qty(b), approved_qty(c)] == [Decimal(1), Decimal("0.5"), None]
    assert c.reason == "rejected: nothing to sell"


# --- state is validated ------------------------------------------------------------
@pytest.mark.parametrize("field", ["equity", "cash", "high_water_mark", "day_start_equity"])
def test_account_amounts_must_be_in_pounds(field: str) -> None:
    kwargs = {
        "equity": gbp(1000),
        "cash": gbp(1000),
        "high_water_mark": gbp(1000),
        "day_start_equity": gbp(1000),
        "holdings": {},
        "quotes": {},
        "quantity_steps": {},
        field: Money(Decimal(100000), Currency.GBX),
    }
    with pytest.raises(CurrencyMismatchError, match=field):
        AccountState(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"equity": "0"},
        {"cash": "-1"},
        {"hwm": "0"},
        {"day_start": "0"},
        {"holdings": {VWRP: Decimal(-1)}},
        {"steps": {VWRP: Decimal(0)}},
    ],
)
def test_impossible_account_states_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="account state"):
        state(**kwargs)  # type: ignore[arg-type]
