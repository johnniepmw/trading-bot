from __future__ import annotations

import decimal
from collections.abc import Mapping
from decimal import Decimal

import pytest

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, CurrencyMismatchError, Money
from tradingbot.risk.limits import Limits
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
    day_start: str | None = None,  # None: same as equity
    holdings: Mapping[Instrument, Decimal] | None = None,
    quotes: Mapping[Instrument, Money] | None = None,
    steps: Mapping[Instrument, Decimal] | None = None,
) -> AccountState:
    return AccountState(
        equity=gbp(equity),
        cash=gbp(cash),
        high_water_mark=gbp(hwm) if hwm is not None else None,
        day_start_equity=gbp(day_start if day_start is not None else equity),
        holdings=holdings if holdings is not None else {VWRP: Decimal(0), SGLN: Decimal(0), SPY: Decimal(0)},
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
    _, d = review([sell("1", SGLN), buy("1")], state(cash="0", holdings={SGLN: Decimal(5), VWRP: Decimal(0)}))
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


# --- review round 1: bad quotes are rejected per order, never crash the run ---------
@pytest.mark.parametrize("bad", [Decimal(0), Decimal(-5)])
def test_a_non_positive_quote_rejects_that_buy_and_keeps_the_sells(bad: Decimal) -> None:
    decisions = review(
        [buy("1"), sell("1", SGLN)],
        state(
            quotes={VWRP: Money(bad, Currency.GBP), SGLN: Money(Decimal(4000), Currency.GBX)},
            holdings={SGLN: Decimal(2), VWRP: Decimal(0)},
        ),
    )
    assert [d.reason for d in decisions] == ["passed", "rejected: bad quote"]


def test_a_quote_in_another_currency_than_the_instrument_is_rejected() -> None:
    # A GBP fund quoted as pence would look 100x cheaper and buy 100x too much.
    [d] = review([buy("1")], state(quotes={VWRP: Money(Decimal(100), Currency.GBX)}))
    assert d.reason == "rejected: quote currency does not match instrument"


def test_a_partial_sell_rounds_down_to_the_step_but_a_full_exit_is_exact() -> None:
    [partial] = review([sell("1.005")], state(holdings={VWRP: Decimal(3)}))
    assert approved_qty(partial) == Decimal("1.00")
    assert partial.reason == "shrunk: to the quantity step"
    [full] = review([sell("5")], state(holdings={VWRP: Decimal("2.345")}))
    assert approved_qty(full) == Decimal("2.345")  # all of it, even off-step
    [tiny] = review([sell("0.004")], state(holdings={VWRP: Decimal(3)}))
    assert tiny.reason == "rejected: below the quantity step"


def test_a_partial_sell_without_a_step_still_goes_through() -> None:
    [d] = review([sell("1")], state(holdings={VWRP: Decimal(3)}, steps={}))
    assert approved_qty(d) == Decimal(1)


def test_divisions_round_down_whatever_the_callers_decimal_context() -> None:
    with decimal.localcontext(prec=3, rounding=decimal.ROUND_CEILING):
        # £250 / £30 = 8.333.. must not round up past the cap
        [d] = review([buy("20")], state(quotes={VWRP: gbp(30)}))
    assert d.approved is not None
    assert d.approved.order.quantity * 30 <= 250


# --- security review round 1 -------------------------------------------------------
def test_a_buy_without_a_holding_record_is_rejected() -> None:
    # A key mismatch (e.g. GBX vs GBP, or a broker ticker) must not read as "holds nothing".
    [d] = review([buy("1")], state(holdings={Instrument("VWRP", Currency.GBX): Decimal(2)}))
    assert d.reason == "rejected: no holding record"


def test_buys_are_vetoed_without_a_day_start_equity() -> None:
    s = AccountState(
        equity=gbp(1000),
        cash=gbp(1000),
        high_water_mark=gbp(1000),
        day_start_equity=None,
        holdings={VWRP: Decimal(0)},
        quotes={VWRP: gbp(100)},
        quantity_steps={VWRP: STEP},
    )
    [d] = review([buy("1")], s)
    assert d.reason == "rejected: no day-start equity"


def test_one_ticker_under_two_currencies_is_refused() -> None:
    with pytest.raises(ValueError, match="account state"):
        state(holdings={VWRP: Decimal(1), Instrument("VWRP", Currency.GBX): Decimal(1)})


@pytest.mark.parametrize(
    "kwargs",
    [{"holdings": {VWRP: Decimal("Infinity")}}, {"steps": {VWRP: Decimal("NaN")}}, {"holdings": {VWRP: 1.0}}],
)
def test_holdings_and_steps_must_be_finite_decimals(kwargs: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError), match="account state"):
        state(**kwargs)  # type: ignore[arg-type]


def test_the_state_cannot_be_changed_after_it_is_checked() -> None:
    holdings = {VWRP: Decimal(0)}
    s = state(holdings=holdings)
    holdings[VWRP] = Decimal(-100)  # the caller's dict changes...
    assert s.holdings[VWRP] == Decimal(0)  # ...the checked copy doesn't
    with pytest.raises(TypeError):
        s.holdings[VWRP] = Decimal(-100)  # type: ignore[index]


def test_errors_stay_trapped_even_if_the_caller_turned_traps_off() -> None:
    ctx = decimal.Context(traps=[])
    with decimal.localcontext(ctx):
        [d] = review([buy("2")], state())
    assert d.approved is not None and d.approved.order.quantity == Decimal(2)


def test_ceilings_hold_even_if_limits_were_tampered_with() -> None:
    limits = Limits()
    object.__setattr__(limits, "position_fraction", Decimal(1))
    object.__setattr__(limits, "drawdown_stop", Decimal("0.9"))
    [capped] = review([buy("10")], state(), limits)
    assert capped.approved is not None and capped.approved.order.quantity == Decimal(3)  # 30% ceiling
    [stopped] = review([buy("1")], state(equity="800", cash="800", hwm="1000"), limits)
    assert stopped.reason == "rejected: drawdown stop"
