"""The daily cycle against the fake broker, and the AC9 failure drills.

Every drill ends with no new orders and an ERROR log line (fail closed, ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.bars import weekdays
from tradingbot.app.cycle import CycleConfig, CycleReport, run_cycle
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import AccountSnapshot, BrokerError
from tradingbot.domain.market import Bar, Instrument, Order, Position, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger, LedgerError
from tradingbot.portfolio.weights import daily_returns, sleeve_weights, vol_scaled
from tradingbot.risk.limits import Limits
from tradingbot.strategy.trend import month_end_days, signals

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
    now = args.pop("now")
    args.setdefault("clock", lambda: now)  # a fixed clock unless a test passes a moving one
    args.setdefault("version", "test")
    args.setdefault("checkpoint", lambda: None)
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


def test_the_ledger_is_checkpointed_around_each_order(broker: FakeBroker, ledger: Ledger) -> None:
    calls: list[int] = []
    cycle(broker, ledger, checkpoint=lambda: calls.append(len(broker.recent_orders())))
    assert calls == [0, 0, 1, 1]  # after reconcile, before the one order is sent, after it, at the end


def test_a_day_with_no_signal_still_saves_the_ledger_at_the_end(broker: FakeBroker, ledger: Ledger) -> None:
    seen: list[tuple[str | None, object]] = []
    cycle(
        broker,
        ledger,
        signal_day=None,
        checkpoint=lambda: seen.append(
            (ledger.runs()[-1].outcome, ledger.previous_day_equity(NOW.date() + timedelta(days=1)))
        ),
    )
    assert seen == [
        ("completed", gbp(1000))
    ]  # the run record and today's equity (and any new peak) are uploaded


def test_the_month_end_marked_acted_is_saved(broker: FakeBroker, ledger: Ledger) -> None:
    seen: list[bool] = []
    cycle(broker, ledger, checkpoint=lambda: seen.append(ledger.signal_acted(SIGNAL_DAY)))
    assert seen[-1] is True


def test_a_failed_final_save_stops_the_run_with_an_alert(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> None:
        raise OSError("bucket unreachable")

    report = cycle(broker, ledger, signal_day=None, checkpoint=fail)
    assert report.stopped == "ledger not saved: OSError: bucket unreachable"
    assert errors(caplog) == ["cycle stopped: ledger not saved: OSError: bucket unreachable"]


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
def assert_stopped(
    ledger: Ledger | None, report: object, broker: FakeBroker, caplog: pytest.LogCaptureFixture, why: str
) -> None:
    """No orders, an ERROR naming why, and (monitoring AC1) a run record stopped for that reason."""
    if ledger is not None:
        record = ledger.runs()[-1]
        assert record.outcome == "stopped" and record.reason is not None and why in record.reason, record
    assert report.sent == 0  # type: ignore[attr-defined]
    assert report.stopped is not None and why in report.stopped  # type: ignore[attr-defined]
    assert any(why in m for m in errors(caplog)), errors(caplog)


def test_drill_kill_switch_off(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    report = cycle(broker, ledger, may_send=lambda: False)
    assert_stopped(ledger, report, broker, caplog, "kill switch")
    assert broker.recent_orders() == []


def test_drill_missing_bar_on_the_signal_day(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)  # held: dropping it from the targets would sell it
    history = FakeHistory()
    history.bars[UP] = [b for b in history.bars[UP] if b.day != SIGNAL_DAY]
    report = cycle(broker, ledger, history=history)
    assert_stopped(ledger, report, broker, caplog, "stale")
    assert broker.recent_orders() == []


def test_drill_hundredfold_price_jump(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    history = FakeHistory()
    history.bars[UP][-60] = replace(history.bars[UP][-60], open=10_000.0, close=10_000.0)
    assert_stopped(ledger, cycle(broker, ledger, history=history), broker, caplog, "jump")
    assert broker.recent_orders() == []


def test_drill_data_source_disagrees_with_the_broker(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.quotes[UP] = Money(broker.quotes[UP].amount * Decimal("1.05"), Currency.GBP)
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "cross-check")
    assert broker.recent_orders() == []


def test_drill_unmapped_broker_position(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker._held[Instrument("MYSTERY", Currency.GBP)] = Decimal(1)
    broker.quotes[Instrument("MYSTERY", Currency.GBP)] = gbp(10)
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "no instrument")
    assert broker.recent_orders() == []


def test_drill_mark_never_seeded(
    broker: FakeBroker, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fresh = Ledger.create(tmp_path / "fresh.sqlite")
    assert_stopped(fresh, cycle(broker, fresh), broker, caplog, "high-water mark")
    fresh.close()
    assert broker.recent_orders() == []


def test_drill_mark_lower_than_recorded(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger._conn.execute("INSERT INTO marks (amount, at) VALUES ('700', ?)", (NOW.isoformat(),))
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "lower than")
    assert broker.recent_orders() == []


def test_drill_unknown_order_from_an_earlier_run(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.mark_unknown(ledger.record_intent("run-0", Order(UP, Side.BUY, Decimal(1)), NOW))
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "not reconciled")
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
    assert_stopped(None, cycle(broker, ledger, now=clock), broker, caplog, "UTC")
    assert broker.recent_orders() == []


def test_drill_signal_day_in_the_future(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    report = cycle(broker, ledger, signal_day=NOW.date())  # bars for "today" can't be complete yet
    assert_stopped(ledger, report, broker, caplog, "signal day")


def test_drill_broker_api_error(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    def down() -> list[Position]:
        raise BrokerError("503 Service Unavailable")

    broker.positions = down  # type: ignore[method-assign]
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "503")


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
    assert again.stopped is not None and "already used" in again.stopped
    assert len(broker.recent_orders()) == 1
    assert any("already used" in m for m in errors(caplog))


def test_a_completed_month_end_repeated_sends_nothing(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    assert (
        cycle(broker, ledger, run_id="run-2") == cycle(broker, ledger, run_id="run-9") == CycleReport(0, None)
    )
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
    assert_stopped(ledger, cycle(broker, ledger, history=history), broker, caplog, "too short")
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
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "equity check")
    assert ledger.high_water_mark() == gbp(1000)
    assert ledger.previous_day_equity(NOW.date() + timedelta(days=1)) == gbp(
        1000
    )  # today's reading not recorded


def test_drill_implausible_new_peak(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker._cash = Decimal(1150)  # 15% above the mark in one go
    assert_stopped(ledger, cycle(broker, ledger, signal_day=None), broker, caplog, "new peak")
    assert ledger.high_water_mark() == gbp(1000)


def test_drill_two_positions_for_one_fund(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    held_up(broker)
    broker.positions = lambda: [Position(UP, Decimal(1)), Position(UP, Decimal(1))]  # type: ignore[method-assign]
    assert_stopped(ledger, cycle(broker, ledger), broker, caplog, "twice")


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
    assert_stopped(ledger, report, broker, caplog, "not verified")
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
    assert_stopped(ledger, report, broker, caplog, "not verified")


@pytest.mark.parametrize("bad", [Money(Decimal(0), Currency.GBP), Money(Decimal(15000), Currency.GBX)])
def test_drill_a_bad_quote_for_a_held_fund_fails_the_equity_check(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture, bad: Money
) -> None:
    held_up(broker)
    real = broker.quote
    broker.quote = lambda i: bad if i == UP else real(i)  # type: ignore[method-assign]
    assert_stopped(ledger, cycle(broker, ledger, signal_day=None), broker, caplog, "equity check")
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
    assert_stopped(ledger, report, broker, caplog, "too old")


def test_an_accepted_order_that_ends_rejected_or_cancelled_alerts(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker.market_open = False  # accepted, queued
    cycle(broker, ledger)
    broker.cancel(broker.recent_orders()[0].order_id)
    caplog.clear()
    cycle(broker, ledger, signal_day=None, now=NOW + DAY, run_id="run-2")
    assert any("CANCELLED" in m for m in errors(caplog))


# --- monitoring step 1: the decision record (docs/exec-plans/active/2026-11-22-monitoring.md) ---
def test_ac1_a_completed_run_is_recorded_with_its_version(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger, version="abc123")
    [record] = ledger.runs()
    assert (record.run_id, record.outcome, record.reason, record.version) == (
        "run-1",
        "completed",
        None,
        "abc123",
    )
    assert record.started_at == NOW and record.ended_at == NOW


def test_ac1_a_run_that_died_is_reported_by_the_next_one(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.start_run("run-0", NOW - DAY, "old")  # started, never finished
    cycle(broker, ledger, signal_day=None)
    assert any("run-0" in m and "never finished" in m for m in errors(caplog))
    assert [r.outcome for r in ledger.runs()] == ["died", "completed"]
    caplog.clear()
    cycle(broker, ledger, signal_day=None, run_id="run-2")
    assert errors(caplog) == []  # reported once


def test_ac2_signal_day_decisions_are_recorded(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    rows = {r.instrument: r for r in ledger.signal_records(SIGNAL_DAY)}
    assert {r.run_id for r in rows.values()} == {"run-1"}
    history = FakeHistory()
    expected_in = signals(history.bars, SIGNAL_DAY)
    before = sleeve_weights(expected_in)
    after = vol_scaled(before, {i: daily_returns(b, SIGNAL_DAY) for i, b in history.bars.items()})
    for inst in (UP, DOWN):
        closes_ = [b.close for b in history.bars[inst] if b.day <= SIGNAL_DAY][-210:]
        assert rows[inst].close == closes_[-1] and rows[inst].sma == pytest.approx(sum(closes_) / 210)
        assert rows[inst].in_trend is expected_in[inst]
        assert (rows[inst].weight_before, rows[inst].weight_after) == (before[inst], after[inst])
    [decision] = ledger.decisions("run-1")
    assert (decision.instrument, decision.side, decision.reason) == (UP, Side.BUY, "passed")
    assert decision.approved_quantity == decision.proposed_quantity and decision.quote == broker.quotes[UP]


def test_ac2_shrinks_are_recorded_and_logged_at_warning(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    broker._cash = Decimal(1000)
    cycle(
        broker,
        ledger,
        config=CycleConfig(universe=CONFIG.universe, limits=Limits(position_fraction=Decimal("0.10"))),
    )
    [decision] = ledger.decisions("run-1")
    assert decision.reason.startswith("shrunk") and decision.approved_quantity < decision.proposed_quantity
    assert any(r.levelno == logging.WARNING and "shrunk" in r.getMessage() for r in caplog.records)


def test_ac3_every_run_stores_a_snapshot(broker: FakeBroker, ledger: Ledger) -> None:
    held_up(broker)
    cycle(broker, ledger, signal_day=None)
    snap = ledger.snapshot("run-1")
    assert snap.cash == gbp(broker._cash)
    rows = {r.instrument: r for r in snap.positions}
    assert rows[UP].quantity == Decimal(2) and rows[UP].quote == broker.quotes[UP]
    assert rows[UP].value_gbp == gbp(2 * broker.quotes[UP].amount)
    assert rows[DOWN].quantity == 0 and rows[DOWN].quote is None  # not held: not quoted on a quiet day
    assert rows[DOWN].value_gbp == gbp(0)
    assert snap.equity_checked


def test_ac3_every_run_stores_a_month_end_preview(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger, signal_day=None)
    preview = ledger.preview("run-1")
    latest = max(b.day for b in FakeHistory().bars[UP] if b.day < NOW.date())
    assert {r.as_of for r in preview} == {latest}
    assert {r.instrument: r.in_trend for r in preview} == {UP: True, DOWN: False}


def test_ac3_a_failing_preview_warns_and_changes_nothing(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("preview broke")

    monkeypatch.setattr("tradingbot.app.cycle._preview", boom)
    report = cycle(broker, ledger)
    assert (report.sent, report.stopped) == (1, None)  # the same orders as without the preview
    assert ledger.runs()[-1].outcome == "completed"
    assert any(r.levelno == logging.WARNING and "preview" in r.getMessage() for r in caplog.records)
    assert errors(caplog) == []


def test_ac1_a_ledger_from_step_7_gains_the_new_tables(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    for table in ("intents", "marks", "daily_equity", "signal_closes", "acted_signals"):
        conn.execute(f"CREATE TABLE {table} (x)")
    conn.commit()
    conn.close()
    opened = Ledger.open(path)
    assert opened.runs() == []
    opened.close()


def test_ssac_is_a_benchmark_and_can_never_be_traded() -> None:
    # monitoring spec, Decisions: SSAC is benchmark-only, never in the trading universe
    with pytest.raises(ValueError, match="SSAC"):
        CycleConfig(universe={UP: Decimal("0.0001"), Instrument("SSAC", Currency.GBP): Decimal("0.0001")})
    with pytest.raises(ValueError, match="SSAC"):
        CycleConfig(universe={Instrument("SSAC", Currency.GBX): Decimal("0.0001")})


# --- monitoring step 1a, review round 1 ------------------------------------------------
def test_ac1_the_end_time_comes_from_the_clock(broker: FakeBroker, ledger: Ledger) -> None:
    ticks = iter([NOW, NOW + timedelta(minutes=20)])
    cycle(broker, ledger, clock=lambda: next(ticks, NOW + timedelta(minutes=20)))
    [record] = ledger.runs()
    assert (record.started_at, record.ended_at) == (NOW, NOW + timedelta(minutes=20))


def test_ac1_a_reused_run_id_reports_the_died_attempt_and_stops(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.start_run("run-1", NOW, "test")  # attempt 1 crashed
    report = cycle(broker, ledger)  # a retry with the same id
    assert report.sent == 0 and "already used" in (report.stopped or "")
    assert any("run-1" in m and "never finished" in m for m in errors(caplog))
    assert [r.outcome for r in ledger.runs()] == ["died"]
    assert broker.recent_orders() == []


def test_ac2_the_scale_factor_is_recorded(broker: FakeBroker, ledger: Ledger) -> None:
    history = FakeHistory()
    noisy = [100 * (1.001**t) * (1.03 if t % 2 else 0.97) for t in range(len(DAYS))]
    history.bars[UP] = [Bar(UP, d, open=c, close=c) for d, c in zip(DAYS, noisy, strict=True)]
    broker.quotes[UP] = Money(Decimal(repr(noisy[DAYS.index(SIGNAL_DAY)])), Currency.GBP)
    cycle(broker, ledger, history=history)
    row = next(r for r in ledger.signal_records(SIGNAL_DAY) if r.instrument == UP)
    assert row.scale < 0.6 and row.scale == pytest.approx(row.weight_after / row.weight_before)


def test_ac3_a_failed_equity_check_still_stores_an_unchecked_snapshot(
    broker: FakeBroker, ledger: Ledger
) -> None:
    held_up(broker)
    real = broker.account
    broker.account = lambda: AccountSnapshot(real().cash, gbp(100_000))  # type: ignore[method-assign]
    cycle(broker, ledger, signal_day=None)
    snap = ledger.snapshot("run-1")
    assert not snap.equity_checked and snap.cash == real().cash


def test_ac3_a_run_stopped_before_reading_the_account_has_no_snapshot(
    broker: FakeBroker, ledger: Ledger
) -> None:
    # recorded deviation: nothing trustworthy to store yet; the run record says why it stopped
    ledger.mark_unknown(ledger.record_intent("run-0", Order(UP, Side.BUY, Decimal(1)), NOW))
    cycle(broker, ledger, signal_day=None)
    with pytest.raises(LedgerError, match="no snapshot"):
        ledger.snapshot("run-1")
    assert "not reconciled" in (ledger.runs()[-1].reason or "")


def test_ac3_the_preview_records_weights(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger, signal_day=None)
    weights = {r.instrument: r.weight for r in ledger.preview("run-1")}
    assert weights == {UP: 0.25, DOWN: 0.0}


@pytest.mark.parametrize("ticker", ["SSAC", "ssac", "SSAC.L", "SSACl_EQ"])
def test_ssac_is_refused_however_its_ticker_is_written(ticker: str) -> None:
    with pytest.raises(ValueError, match="benchmark"):
        CycleConfig(universe={Instrument(ticker, Currency.GBP): Decimal("0.0001")})


def test_the_universe_cant_be_changed_after_the_check() -> None:
    universe = {UP: Decimal("0.0001")}
    config = CycleConfig(universe=universe)
    universe[Instrument("SSAC", Currency.GBP)] = Decimal(1)  # the caller's dict changes...
    assert list(config.universe) == [UP]  # ...the checked copy doesn't


def test_ac3_an_unusable_broker_quote_is_not_recorded_as_a_price(broker: FakeBroker, ledger: Ledger) -> None:
    held_up(broker)
    real = broker.quote
    broker.quote = lambda i: Money(Decimal(15000), Currency.GBX) if i == UP else real(i)  # type: ignore[method-assign]
    cycle(broker, ledger, signal_day=None)
    row = next(r for r in ledger.snapshot("run-1").positions if r.instrument == UP)
    assert (row.quote, row.value_gbp) == (None, None)


def test_a_clock_that_fails_at_the_end_still_returns_the_report(broker: FakeBroker, ledger: Ledger) -> None:
    ticks = iter([NOW])

    def clock() -> datetime:
        return next(ticks)  # StopIteration on the second read

    report = cycle(broker, ledger, clock=clock)
    assert report.sent == 1  # the orders went out and the report comes back


def test_a_drawdown_crossing_is_alerted(ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    ledger.record_equity(NOW.date() - timedelta(days=1), gbp(960))  # 4% below the mark yesterday
    poorer = FakeBroker(cash=gbp(940), quotes={UP: gbp(100), DOWN: Money(Decimal(5000), Currency.GBX)})
    poorer.now = lambda: NOW
    with caplog.at_level(logging.WARNING):
        cycle(poorer, ledger, signal_day=None)
    [crossing] = [r for r in caplog.records if getattr(r, "alert", None) == "drawdown"]
    assert crossing.getMessage().startswith("drawdown crossed 5%")


def test_a_dry_runs_drawdown_crossing_alerts_nothing(
    ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    from tradingbot.app.alerts import alert_kind  # noqa: PLC0415
    from tradingbot.app.cycle import dry_run_cycle  # noqa: PLC0415

    ledger.record_equity(NOW.date() - timedelta(days=1), gbp(960))
    poorer = FakeBroker(cash=gbp(940), quotes={UP: gbp(100), DOWN: Money(Decimal(5000), Currency.GBX)})
    poorer.now = lambda: NOW
    with caplog.at_level(logging.DEBUG):
        dry_run_cycle(
            broker=poorer,
            ledger=ledger,
            history=FakeHistory(),
            config=CONFIG,
            signal_day=None,
            clock=lambda: NOW,
            run_id="dry-1",
        )
    assert any("drawdown crossed 5%" in r.getMessage() for r in caplog.records)  # reported, as a warning
    assert {alert_kind(r) for r in caplog.records} == {None}  # but pages nobody


def test_a_crossing_after_days_without_runs_is_still_alerted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    quiet = Ledger.create(tmp_path / "quiet.sqlite")
    quiet.seed_high_water_mark(gbp(1000), NOW - timedelta(days=9))  # an outage: nothing recorded since
    poorer = FakeBroker(cash=gbp(940), quotes={UP: gbp(100), DOWN: Money(Decimal(5000), Currency.GBX)})
    poorer.now = lambda: NOW
    with caplog.at_level(logging.WARNING):
        cycle(poorer, quiet, signal_day=None)
    quiet.close()
    assert [r.getMessage() for r in caplog.records if getattr(r, "alert", None) == "drawdown"] == [
        "drawdown crossed 5% below the high-water mark"
    ]
