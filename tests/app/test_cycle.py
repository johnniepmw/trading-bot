"""The daily cycle against the fake broker, and the AC9 failure drills.

Every drill ends with no new orders and an ERROR log line (fail closed, ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.bars import weekdays
from tradingbot.app.cycle import CycleConfig, run_cycle
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import BrokerError
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
    opened.seed_high_water_mark(gbp(1000), NOW)  # the owner's setup step
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
    broker._cash = Decimal(1200)  # equity rose
    cycle(broker, ledger, signal_day=None)
    assert ledger.high_water_mark() == gbp(1200)
    assert ledger.day_start_equity(NOW.date(), gbp(1)) == gbp(1200)


# --- drills: each one sends nothing and logs an ERROR --------------------------------
def assert_stopped(report: object, broker: FakeBroker, caplog: pytest.LogCaptureFixture, why: str) -> None:
    assert report.sent == 0  # type: ignore[attr-defined]
    assert report.stopped is not None and why in report.stopped  # type: ignore[attr-defined]
    assert broker.recent_orders() == [] or all(o.order_id for o in broker.recent_orders())
    assert any(why in m for m in errors(caplog)), errors(caplog)


def test_drill_kill_switch_off(broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture) -> None:
    report = cycle(broker, ledger, may_send=lambda: False)
    assert_stopped(report, broker, caplog, "kill switch")
    assert broker.recent_orders() == []


def test_drill_missing_bar_on_the_signal_day(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    history = FakeHistory()
    history.bars[DOWN] = [b for b in history.bars[DOWN] if b.day != SIGNAL_DAY]
    report = cycle(broker, ledger, history=history)
    assert_stopped(report, broker, caplog, "stale")
    assert broker.recent_orders() == []  # never: drop DOWN from the targets and sell it


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
        datetime(2025, 12, 2, 9, 0),
        datetime(2025, 12, 2, 9, 0, tzinfo=ZoneInfo("Europe/London")),
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


def test_drill_duplicate_run(broker: FakeBroker, ledger: Ledger) -> None:
    cycle(broker, ledger)
    assert cycle(broker, ledger).stopped == "run run-1 already executed"
    assert len(broker.recent_orders()) == 1


def test_drill_daily_loss_pauses_buys(
    broker: FakeBroker, ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.day_start_equity(NOW.date(), gbp(1030))  # equity fell 3% since the day started
    report = cycle(broker, ledger)
    assert report.sent == 0  # risk rejected the buy
    assert any("daily loss" in m for m in errors(caplog))
