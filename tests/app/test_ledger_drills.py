"""Drills: a run dies or loses a race mid-send; the next run never sends anything twice (Phase 2 AC2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.app.fake_store import FakeStore
from tradingbot.app.remote_ledger import LEDGER_OBJECT, LOCK_OBJECT, LockHolder, LostRaceError, RemoteLedger
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.execution.executor import execute, reconcile
from tradingbot.ledger.store import IntentState, Ledger

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
NOW = datetime(2026, 11, 23, 7, 15, tzinfo=UTC)
LEDGERS, LOCKS = "ledgers", "locks"


class Crash(BaseException):
    """The process dies: nothing after this point runs, not even `release`."""


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


def buy(qty: str, inst: Instrument = VWRP) -> ApprovedOrder:
    return ApprovedOrder(Order(inst, Side.BUY, Decimal(qty)))


@pytest.fixture
def broker() -> FakeBroker:
    fake = FakeBroker(cash=gbp("1000"), quotes={VWRP: gbp("100"), SGLN: Money(Decimal(4000), Currency.GBX)})
    fake.now = lambda: NOW
    return fake


@pytest.fixture
def store(tmp_path: Path) -> FakeStore:
    fake = FakeStore(lambda: NOW)
    setup = remote(fake, tmp_path / "setup")
    setup.acquire(LockHolder("setup", "setup-1", "setup"))
    setup.create().seed_high_water_mark(gbp("1000"), NOW - timedelta(days=1))
    setup.checkpoint()
    setup.release()
    return fake


def remote(store: FakeStore, workdir: Path) -> RemoteLedger:
    workdir.mkdir()
    return RemoteLedger(
        store,
        ledger_bucket=LEDGERS,
        lock_bucket=LOCKS,
        workdir=workdir,
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def run_until_crash(
    store: FakeStore, broker: FakeBroker, workdir: Path, orders: list[ApprovedOrder], crash: dict[str, int]
) -> None:
    """A run whose process dies at a checkpoint: `before_upload` or `after_upload` names which one."""
    run = remote(store, workdir)
    run.acquire(LockHolder("exec-1", "run-1", "run"))
    ledger = run.open()
    calls = 0

    def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == crash.get("before_upload"):
            raise Crash
        run.checkpoint()
        if calls == crash.get("after_upload"):
            raise Crash

    with pytest.raises(Crash):
        execute(orders, broker, ledger, run_id="run-1", now=NOW, may_send=lambda: True, checkpoint=checkpoint)
    store.remove(LOCKS, LOCK_OBJECT)  # the owner's `ops unlock` after the stale-lock alert


def next_run(store: FakeStore, broker: FakeBroker, workdir: Path, orders: list[ApprovedOrder]) -> Ledger:
    run = remote(store, workdir)
    run.acquire(LockHolder("exec-2", "run-2", "run"))
    ledger = run.open()
    execute(orders, broker, ledger, run_id="run-2", now=NOW, may_send=lambda: True, checkpoint=run.checkpoint)
    return ledger


def test_drill_crash_after_the_intent_is_saved_but_before_sending(
    store: FakeStore, broker: FakeBroker, tmp_path: Path
) -> None:
    run_until_crash(store, broker, tmp_path / "one", [buy("1")], {"after_upload": 2})
    assert broker.recent_orders() == []  # died before `place`
    ledger = next_run(store, broker, tmp_path / "two", [buy("1")])
    assert len(broker.recent_orders()) == 1  # sent once, by the second run
    assert [i.state for i in ledger.settled()] == [IntentState.NOT_SENT]  # the first run's intent
    assert [i.state for i in ledger.open_intents()] == [IntentState.SENT]  # the second run's


def test_drill_crash_after_sending_before_the_next_upload(
    store: FakeStore, broker: FakeBroker, tmp_path: Path
) -> None:
    run_until_crash(store, broker, tmp_path / "one", [buy("1"), buy("1", SGLN)], {"before_upload": 3})
    assert len(broker.recent_orders()) == 1  # the first order went out; its "sent" wasn't saved
    run = remote(store, tmp_path / "two")
    run.acquire(LockHolder("exec-2", "run-2", "run"))
    ledger = run.open()
    [saved] = ledger.open_intents()
    assert saved.state is IntentState.INTENDED  # the remote copy still says intended
    assert reconcile(broker, ledger).clean  # matched to the broker's order, not resent
    assert [i.state for i in ledger.settled()] == [IntentState.FILLED]
    assert len(broker.recent_orders()) == 1


@pytest.mark.parametrize(
    ("checkpoint", "sent"),
    [(1, 0), (2, 0), (3, 1), (4, 1)],  # after reconcile, before order 1, after order 1, before order 2
)
def test_drill_a_lost_race_at_any_checkpoint_stops_the_run(
    store: FakeStore, broker: FakeBroker, tmp_path: Path, checkpoint: int, sent: int
) -> None:
    run = remote(store, tmp_path / "run")
    run.acquire(LockHolder("exec-1", "run-1", "run"))
    ledger = run.open()
    calls = 0

    def rival_then_upload() -> None:
        nonlocal calls
        calls += 1
        if calls == checkpoint:
            store.put(LEDGERS, LEDGER_OBJECT, store.data(LEDGERS, LEDGER_OBJECT) or b"")  # another writer
        run.checkpoint()

    with pytest.raises(LostRaceError):
        execute(
            [buy("1"), buy("1", SGLN)],
            broker,
            ledger,
            run_id="run-1",
            now=NOW,
            may_send=lambda: True,
            checkpoint=rival_then_upload,
        )
    assert len(broker.recent_orders()) == sent  # nothing is sent after the lost race
