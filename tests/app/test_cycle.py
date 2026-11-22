"""The daily cycle against the fake broker, and the AC9 failure drills.

Every drill ends with no new orders and an ERROR log line (fail closed, ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.bars import weekdays
from tradingbot.app.cycle import CycleConfig, run_cycle
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import AccountSnapshot, BrokerError
from tradingbot.domain.market import Bar, Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger
from tradingbot.strategy.trend import month_end_days

UP = Instrument("UP", Currency.GBP)  # trends up: held
DOWN = Instrument("DOWN", Currency.GBX)  # trends down, quoted in pence: cash
DAYS = weekdays(date(2025, 1, 1), 300)
SIGNAL_DAY = max(month_end_days(DAYS))
NOW = datetime.combine(DAYS[DAYS.index(SIGNAL_DAY) + 1], datetime.min.time(), UTC).replace(hour=9)


def gbp(x: str | int) -> Money:
    return Money(Decimal(x), Currency.GBP)


def closes(inst: Instrument) -> list[float]:
    return [100 + t * 0.2 if inst == UP else 5000 - t * 2.0 for t in range(len(DAYS))]


class FakeHistory:
    def __init__(self) -> None:
        self.bars = {
            i: [Bar(i, d, open=c, close=c) for d, c in zip(DAYS, closes(i), strict=True)] for i in (UP, DOWN)
        }

    def daily_bars(self, instrument: Instrument, start: date, end: date) -> Sequence[Bar]:
        return [b for b in self.bars[instrument] if start <= b.day <= end]


def close_on(inst: Instrument, day: date) -> float:
    return closes(inst)[DAYS.index(day)]


@pytest.fixture
def broker() -> FakeBroker:
    fake = FakeBroker(
        cash=gbp(1000),
        quotes={
            UP: Money(Decimal(repr(close_on(UP, SIGNAL_DAY))), Currency.GBP),
            DOWN: Money(Decimal(repr(close_on(DOWN, SIGNAL_DAY))), Currency.GBX),
        },
    )
    fake.now = lambda: NOW
    return fake


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.create(tmp_path / "ledger.sqlite")
    opened.seed_high_water_mark(gbp(1000), NOW - timedelta(days=1))  # the owner's setup step, the day before
    yield opened
    opened.close()


CONFIG = CycleConfig(universe={UP: Decimal("0.0001"), DOWN: Decimal("0.0001")})


def cycle(broker: FakeBroker, ledger: Ledger, **kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "broker": broker,
        "ledger": ledger,
        "history": FakeHistory(),
        "config": CONFIG,
        "signal_day": SIGNAL_DAY,
        "now": NOW,
        "run_id": "run-1",
        "may_send": lambda: True,
    }
    args.update(kw)
    return run_cycle(**args)  # type: ignore[arg-type]


def errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


# --- the happy path ----------------------------------------------------------------
def test_a_month_end_buys_the_trending_fund_and_leaves_the_other_in_cash(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    report = cycle(broker, ledger)
    assert (report.sent, report.stopped) == (1, None)
    [order] = broker.recent_orders()
    assert (order.order.instrument, order.order.side) == (UP, Side.BUY)
    assert order.order.quantity * Decimal(repr(close_on(UP, SIGNAL_DAY))) <= Decimal(250)  # the 25% sleeve
    assert errors(caplog) == []


def test_running_again_is_harmless(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    again = cycle(broker, ledger, run_id="run-2")
    assert again.sent == 0 and again.stopped is None  # on target: nothing to do
    assert len(broker.recent_orders()) == 1


def test_a_day_without_a_signal_only_reconciles_and_marks(broker: FakeBroker, ledger: Ledger) -> None:
    report = cycle(broker, ledger, signal_day=None)
    assert (report.sent, report.stopped) == (0, None)
    assert broker.recent_orders() == []


def test_the_high_water_mark_and_day_start_are_kept(broker: FakeBroker, ledger: Ledger) -> None:
    broker._cash = Decimal(1050)  # equity rose 5%
    cycle(broker, ledger, signal_day=None)
    assert ledger.high_water_mark() == gbp(1050)
    assert ledger.previous_day_equity(NOW.date() + timedelta(days=1)) == gbp(1050)


# --- drills: each one sends nothing and logs an ERROR --------------------------------
def assert_stopped(report: object, broker: FakeBroker, caplog: pytest.LogCaptureFixture, why: str) -> None:
    assert report.sent == 0  # type: ignore[attr-defined]
    assert report.stopped is not None and why in report.stopped  # type: ignore[attr-defined]
    assert any(why in m for m in errors(caplog)), errors(caplog)


def test_drill_kill_switch_off(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    report = cycle(broker, ledger, may_send=lambda: False)
    assert_stopped(report, broker, caplog, "kill switch")
    assert broker.recent_orders() == []


def test_drill_missing_bar_on_the_signal_day(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)  # held: dropping it from the targets would sell it
    history = FakeHistory()
    history.bars[UP] = [b for b in history.bars[UP] if b.day != SIGNAL_DAY]
    report = cycle(broker, ledger, history=history)
    assert_stopped(report, broker, caplog, "stale")
    assert broker.recent_orders() == []


def test_drill_hundredfold_price_jump(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    history = FakeHistory()
    history.bars[UP][-60] = replace(history.bars[UP][-60], open=10_000.0, close=10_000.0)
    assert_stopped(cycle(broker, ledger, history=history), broker, caplog, "jump")
    assert broker.recent_orders() == []


def test_drill_data_source_disagrees_with_the_broker(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.quotes[UP] = Money(broker.quotes[UP].amount * Decimal("1.05"), Currency.GBP)
    assert_stopped(cycle(broker, ledger), broker, caplog, "cross-check")
    assert broker.recent_orders() == []


def test_drill_unmapped_broker_position(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker._held[Instrument("MYSTERY", Currency.GBP)] = Decimal(1)
    broker.quotes[Instrument("MYSTERY", Currency.GBP)] = gbp(10)
    assert_stopped(cycle(broker, ledger), broker, caplog, "no instrument")
    assert broker.recent_orders() == []


def test_drill_mark_never_seeded(
    broker: FakeBroker, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fresh = Ledger.create(tmp_path / "fresh.sqlite")
    assert_stopped(cycle(broker, fresh), broker, caplog, "high-water mark")
    fresh.close()
    assert broker.recent_orders() == []


def test_drill_mark_lower_than_recorded(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger._conn.execute("INSERT INTO marks (amount, at) VALUES ('700', ?)", (NOW.isoformat(),))
    assert_stopped(cycle(broker, ledger), broker, caplog, "lower than")
    assert broker.recent_orders() == []


def test_drill_unknown_order_from_an_earlier_run(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.mark_unknown(ledger.record_intent("run-0", Order(UP, Side.BUY, Decimal(1)), NOW))
    assert_stopped(cycle(broker, ledger), broker, caplog, "not reconciled")
    assert broker.recent_orders() == []


@pytest.mark.parametrize(
    "clock",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(ZoneInfo("Europe/London")),  # same instant, zero offset in winter
    ],
)
def test_drill_clock_not_in_utc(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture, clock: datetime
) -> None:
    assert_stopped(cycle(broker, ledger, now=clock), broker, caplog, "UTC")
    assert broker.recent_orders() == []


def test_drill_signal_day_in_the_future(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    report = cycle(broker, ledger, signal_day=NOW.date())  # bars for "today" can't be complete yet
    assert_stopped(report, broker, caplog, "signal day")


def test_drill_broker_api_error(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    def down() -> list[Position]:
        raise BrokerError("503 Service Unavailable")

    broker.positions = down  # type: ignore[method-assign]
    assert_stopped(cycle(broker, ledger), broker, caplog, "503")


def test_drill_timeout_while_sending(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.timeout_after_accepting_next = True
    report = cycle(broker, ledger)
    assert report.sent == 0 and report.stopped is not None and "timeout" in report.stopped
    assert [i.state for i in ledger.open_intents()] == [IntentState.UNKNOWN]
    assert any("timeout" in m for m in errors(caplog))
    # the next run finds it at the broker and doesn't buy again
    assert cycle(broker, ledger, run_id="run-2").sent == 0
    assert len(broker.recent_orders()) == 1


def test_drill_partial_fill_blocks_until_it_finishes(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.partial_fill_next = Decimal("0.5")
    cycle(broker, ledger)
    blocked = cycle(broker, ledger, run_id="run-2")
    assert blocked.sent == 0 and "not reconciled" in (blocked.stopped or "")
    assert len(broker.recent_orders()) == 1
    assert any("not reconciled" in m for m in errors(caplog))


def test_drill_duplicate_run(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    broker.timeout_after_accepting_next = True
    cycle(broker, ledger)  # stops after sending: the month end is not marked acted
    again = cycle(broker, ledger)  # the same run retried
    assert again.stopped == "run run-1 already executed"
    assert len(broker.recent_orders()) == 1
    assert any("already executed" in m for m in errors(caplog))


def test_a_completed_month_end_repeated_sends_nothing(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    assert cycle(broker, ledger) == cycle(broker, ledger, run_id="run-9")
    assert len(broker.recent_orders()) == 1


def test_drill_daily_loss_pauses_buys(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.record_equity(NOW.date() - timedelta(days=1), gbp(1030))  # yesterday's run saw £1030
    report = cycle(broker, ledger)  # today £1000: down 2.9%
    assert report.sent == 0  # risk rejected the buy
    assert any("daily loss" in m for m in errors(caplog))


def test_the_daily_loss_check_works_across_real_runs(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    cycle(broker, ledger, signal_day=None, now=NOW - timedelta(days=1), run_id="run-0")  # £1000 recorded
    broker._cash = Decimal(970)  # equity falls 3% overnight
    report = cycle(broker, ledger, run_id="run-2")  # the first run after the month end
    assert report.sent == 0
    assert any("daily loss" in m for m in errors(caplog))


def test_an_unreconciled_ledger_leaves_the_mark_and_day_start_alone(
    broker: FakeBroker, ledger: Ledger
) -> None:
    ledger.mark_unknown(ledger.record_intent("run-0", Order(UP, Side.BUY, Decimal(1)), NOW))
    broker._cash = Decimal(1200)
    cycle(broker, ledger, signal_day=None)
    assert ledger.high_water_mark() == gbp(1000)
    assert ledger.previous_day_equity(NOW.date() + timedelta(days=1)) == gbp(
        1000
    )  # today's reading not recorded


def test_drill_short_history_for_a_held_fund_sells_nothing(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)
    history = FakeHistory()
    history.bars[UP] = history.bars[UP][-120:]  # a paging hiccup: too few bars for the average
    assert_stopped(cycle(broker, ledger, history=history), broker, caplog, "too short")
    assert broker.recent_orders() == []


@pytest.mark.parametrize(("ratio", "stops"), [("1.019", False), ("1.021", True), ("0.979", True)])
def test_the_cross_check_tolerance_is_2_percent(
    broker: FakeBroker, ledger: Ledger, ratio: str, stops: bool
) -> None:
    broker.quotes[UP] = Money(broker.quotes[UP].amount * Decimal(ratio), Currency.GBP)
    report = cycle(broker, ledger)
    assert ("cross-check" in (report.stopped or "")) is stops


def test_a_quote_in_the_wrong_currency_fails_the_cross_check(broker: FakeBroker, ledger: Ledger) -> None:
    broker.quotes[DOWN] = Money(broker.quotes[DOWN].amount / 100, Currency.GBP)  # pence fund quoted in pounds
    assert "cross-check" in (cycle(broker, ledger).stopped or "")


def test_the_volatility_cap_applies_in_the_cycle(broker: FakeBroker, ledger: Ledger) -> None:
    history = FakeHistory()
    noisy = [100 * (1.001**t) * (1.03 if t % 2 else 0.97) for t in range(len(DAYS))]  # uptrend, ~48% a year
    history.bars[UP] = [Bar(UP, d, open=c, close=c) for d, c in zip(DAYS, noisy, strict=True)]
    broker.quotes[UP] = Money(Decimal(repr(noisy[DAYS.index(SIGNAL_DAY)])), Currency.GBP)
    cycle(broker, ledger, history=history)
    [order] = broker.recent_orders()
    assert order.order.quantity * broker.quotes[UP].amount < Decimal(150)  # well below the £250 sleeve


def test_a_rotation_sells_first_and_buys_only_with_cash_held_before(
    broker: FakeBroker, ledger: Ledger
) -> None:
    history = FakeHistory()
    n = len(DAYS)
    falling = [100 + t * 0.2 if t < n - 40 else 100 + (n - 40) * 0.2 - (t - n + 40) * 2.0 for t in range(n)]
    rising = [5000 - t * 2.0 if t < n - 40 else 5000 - (n - 40) * 2.0 + (t - n + 40) * 40.0 for t in range(n)]
    for inst, cs in ((UP, falling), (DOWN, rising)):
        history.bars[inst] = [Bar(inst, d, open=c, close=c) for d, c in zip(DAYS, cs, strict=True)]
    k = DAYS.index(SIGNAL_DAY)
    broker.quotes = {
        UP: Money(Decimal(repr(falling[k])), Currency.GBP),
        DOWN: Money(Decimal(repr(rising[k])), Currency.GBX),
    }
    broker._held[UP] = (Decimal(950) / broker.quotes[UP].amount).quantize(Decimal("0.0001"))
    broker._cash = Decimal(50)  # equity about £1000; the sale's proceeds must not fund the buy
    cycle(broker, ledger, history=history)
    sides = [(o.order.instrument, o.order.side) for o in broker.recent_orders()]
    assert sides == [(UP, Side.SELL), (DOWN, Side.BUY)]
    buy = broker.recent_orders()[1].order
    assert buy.quantity * broker.quotes[DOWN].to_gbp().amount <= Decimal(50)


# --- security review round 1: the broker's equity is checked before it's trusted -----
def held_up(broker: FakeBroker) -> None:
    broker._held[UP] = Decimal(2)
    broker._cash = Decimal(1000) - 2 * broker.quotes[UP].amount  # equity still £1000


@pytest.mark.parametrize("reported", [Decimal(100_000), Decimal(1000) - Decimal(2) * Decimal(150)])
def test_drill_broker_equity_that_doesnt_add_up(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture, reported: Decimal
) -> None:
    held_up(broker)
    real = broker.account
    broker.account = lambda: AccountSnapshot(real().cash, gbp(reported))  # type: ignore[method-assign]
    assert_stopped(cycle(broker, ledger), broker, caplog, "equity check")
    assert ledger.high_water_mark() == gbp(1000)
    assert ledger.previous_day_equity(NOW.date() + timedelta(days=1)) == gbp(
        1000
    )  # today's reading not recorded


def test_drill_implausible_new_peak(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker._cash = Decimal(1150)  # 15% above the mark in one go
    assert_stopped(cycle(broker, ledger, signal_day=None), broker, caplog, "new peak")
    assert ledger.high_water_mark() == gbp(1000)


def test_drill_two_positions_for_one_fund(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)
    broker.positions = lambda: [Position(UP, Decimal(1)), Position(UP, Decimal(1))]  # type: ignore[method-assign]
    assert_stopped(cycle(broker, ledger), broker, caplog, "twice")


# --- review round 2: one decision per month end, on verified closes ------------------
DAY = timedelta(days=1)


def test_a_month_end_is_acted_on_once_no_mid_month_rebalancing(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    [bought] = broker.recent_orders()
    broker._held[UP] = bought.filled_quantity / 2  # the holding drifts far below target...
    broker._cash += bought.filled_quantity / 2 * broker.quotes[UP].amount
    report = cycle(broker, ledger, now=NOW + 4 * DAY, run_id="run-2")
    assert (report.sent, report.stopped) == (0, None)  # ...but the next rebalance is next month end
    assert len(broker.recent_orders()) == 1


def test_a_deferred_month_end_is_acted_on_later_with_the_verified_closes(
    broker: FakeBroker, ledger: Ledger
) -> None:
    assert cycle(broker, ledger, may_send=lambda: False).sent == 0  # day 1: closes verified, nothing sent
    report = cycle(broker, ledger, now=NOW + DAY, run_id="run-2")
    assert report.sent == 1


def test_drill_a_later_day_without_verified_closes_stops(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    report = cycle(broker, ledger, now=NOW + 3 * DAY)  # the run after the month end never happened
    assert_stopped(report, broker, caplog, "not verified")
    assert broker.recent_orders() == []


def test_drill_a_bad_month_end_print_never_flips_the_signal(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)
    history = FakeHistory()
    k = DAYS.index(SIGNAL_DAY)
    bad = history.bars[UP][k]
    history.bars[UP][k] = replace(
        bad, open=bad.open * 0.9, close=bad.close * 0.9
    )  # would read as a trend break
    first = cycle(broker, ledger, history=history)  # day 1: cross-check fails
    second = cycle(broker, ledger, history=history, now=NOW + DAY, run_id="run-2")  # day 2: never verified
    assert "cross-check" in (first.stopped or "") and "not verified" in (second.stopped or "")
    assert broker.recent_orders() == []  # the held fund was never sold


def test_drill_a_revised_month_end_close_stops(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    cycle(broker, ledger, may_send=lambda: False)  # day 1 verifies the closes
    history = FakeHistory()
    k = DAYS.index(SIGNAL_DAY)
    history.bars[UP][k] = replace(history.bars[UP][k], close=history.bars[UP][k].close * 1.01)
    report = cycle(broker, ledger, history=history, now=NOW + DAY, run_id="run-2")
    assert_stopped(report, broker, caplog, "not verified")


@pytest.mark.parametrize("bad", [Money(Decimal(0), Currency.GBP), Money(Decimal(15000), Currency.GBX)])
def test_drill_a_bad_quote_for_a_held_fund_fails_the_equity_check(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture, bad: Money
) -> None:
    held_up(broker)
    real = broker.quote
    broker.quote = lambda i: bad if i == UP else real(i)  # type: ignore[method-assign]
    assert_stopped(cycle(broker, ledger, signal_day=None), broker, caplog, "equity check")
    assert ledger.previous_day_equity(NOW.date() + DAY) == gbp(1000)


def test_a_day_start_older_than_five_days_counts_as_unknown(
    tmp_path: Path, broker: FakeBroker, caplog: pytest.LogCaptureFixture
) -> None:
    old = Ledger.create(tmp_path / "old.sqlite")
    old.seed_high_water_mark(gbp(1000), NOW - 10 * DAY)  # nothing recorded since
    report = cycle(broker, old)
    old.close()
    assert report.sent == 0
    assert any("no day-start equity" in m for m in errors(caplog))


def test_no_signal_days_only_need_quotes_for_funds_held(broker: FakeBroker, ledger: Ledger) -> None:
    del broker.quotes[DOWN]  # if the broker can't price a fund we don't hold
    assert cycle(broker, ledger, signal_day=None).stopped is None


@pytest.mark.parametrize(("change", "acts"), [(1.004, True), (1.006, False)])
def test_verified_closes_allow_a_small_rescale(
    broker: FakeBroker, ledger: Ledger, change: float, acts: bool
) -> None:
    # adjusted (total-return) histories are rescaled when a fund goes ex-dividend
    cycle(broker, ledger, may_send=lambda: False)  # day 1 verifies the closes
    history = FakeHistory()
    for inst in (UP, DOWN):
        history.bars[inst] = [
            replace(b, open=b.open * change, close=b.close * change) for b in history.bars[inst]
        ]
    broker.quotes = {i: Money(q.amount * Decimal(repr(change)), q.currency) for i, q in broker.quotes.items()}
    report = cycle(broker, ledger, history=history, now=NOW + DAY, run_id="run-2")
    assert (report.sent == 1) is acts


def test_drill_a_signal_day_more_than_35_days_old_stops(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    report = cycle(broker, ledger, now=NOW + 40 * DAY)  # a calendar bug reviving an old month end
    assert_stopped(report, broker, caplog, "too old")


def test_an_accepted_order_that_ends_rejected_or_cancelled_alerts(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.market_open = False  # accepted, queued
    cycle(broker, ledger)
    broker.cancel(broker.recent_orders()[0].order_id)
    caplog.clear()
    cycle(broker, ledger, signal_day=None, now=NOW + DAY, run_id="run-2")
    assert any("CANCELLED" in m for m in errors(caplog))
