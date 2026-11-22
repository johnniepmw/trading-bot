"""The ledger in object storage: lock, checkpoints, reference mark (ADR-0017; Phase 2 AC1 to AC3)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.app.fake_store import FakeStore
from tradingbot.app.remote_ledger import (
    LEDGER_OBJECT,
    LOCK_OBJECT,
    MARK_OBJECT,
    STALE_AFTER,
    LedgerStorageError,
    LockHeldError,
    LockHolder,
    LostRaceError,
    RemoteLedger,
    RestoredLedgerError,
    SetupRefusedError,
    StaleLockError,
)
from tradingbot.domain.money import Currency, Money

LEDGERS, LOCKS = "ledger-bucket", "lock-bucket"
NOW = datetime(2026, 11, 23, 7, 15, tzinfo=UTC)
HOLDER = LockHolder(execution="trading-bot-prod-abc12", run_id="run-1", holder="run")


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> FakeStore:
    return FakeStore(clock)


def remote(store: FakeStore, clock: Clock, workdir: Path) -> RemoteLedger:
    workdir.mkdir(parents=True, exist_ok=True)
    return RemoteLedger(
        store, ledger_bucket=LEDGERS, lock_bucket=LOCKS, workdir=workdir, clock=clock, sleep=lambda _: None
    )


def set_up(store: FakeStore, clock: Clock, tmp_path: Path, mark: str = "1000") -> None:
    """The owner's ledger setup: create, seed the mark, upload, release."""
    setup = remote(store, clock, tmp_path / "setup")
    setup.acquire(LockHolder("setup-exec", "setup-1", "setup"))
    ledger = setup.create()
    ledger.seed_high_water_mark(gbp(mark), NOW - timedelta(days=1))
    setup.checkpoint()
    setup.release()


@pytest.fixture
def ready(store: FakeStore, clock: Clock, tmp_path: Path) -> FakeStore:
    set_up(store, clock, tmp_path)
    return store


# --- AC1: one run at a time ---------------------------------------------------------
def test_a_run_takes_the_lock_with_who_holds_it(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    body = json.loads(ready.data(LOCKS, LOCK_OBJECT) or b"")
    assert body == {"execution": "trading-bot-prod-abc12", "run_id": "run-1", "holder": "run"}


def test_releasing_removes_the_lock(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()
    run.release()
    assert ready.data(LOCKS, LOCK_OBJECT) is None


def test_a_second_run_is_refused_while_the_first_holds_the_lock(
    ready: FakeStore, clock: Clock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    remote(ready, clock, tmp_path / "first").acquire(HOLDER)
    clock.now += timedelta(minutes=5)
    with pytest.raises(LockHeldError), caplog.at_level(logging.WARNING):
        remote(ready, clock, tmp_path / "second").acquire(LockHolder("exec-2", "run-2", "run"))
    [line] = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert line.levelno == logging.WARNING  # a duplicate trigger is not an alert
    assert line.getMessage().startswith("run skipped: lock held by trading-bot-prod-abc12 (run run-1)")
    assert "cycle stopped" not in line.getMessage()


def test_a_stale_lock_stops_the_run_with_an_alert(
    ready: FakeStore, clock: Clock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    remote(ready, clock, tmp_path / "crashed").acquire(HOLDER)  # never released
    clock.now += STALE_AFTER + timedelta(seconds=1)
    with pytest.raises(StaleLockError), caplog.at_level(logging.WARNING):
        remote(ready, clock, tmp_path / "next").acquire(LockHolder("exec-2", "run-2", "run"))
    [line] = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert line.levelno == logging.ERROR
    assert line.getMessage().startswith("stale lock: held by trading-bot-prod-abc12 (run run-1)")
    assert ready.data(LOCKS, LOCK_OBJECT) is not None  # never cleared by a run (`ops unlock`)


def test_staleness_is_measured_from_the_lock_objects_creation(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    remote(ready, clock, tmp_path / "first").acquire(HOLDER)
    clock.now += STALE_AFTER
    with pytest.raises(LockHeldError):  # exactly at the limit: not yet stale
        remote(ready, clock, tmp_path / "second").acquire(LockHolder("exec-2", "run-2", "run"))


def test_a_lock_that_vanishes_while_being_read_still_refuses(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    ready.put(LOCKS, LOCK_OBJECT, b"{}")
    ready.info = lambda bucket, name: None  # type: ignore[method-assign] - released in between
    with pytest.raises(LockHeldError):
        remote(ready, clock, tmp_path / "run").acquire(HOLDER)


def test_a_retried_lock_write_that_had_landed_counts_as_ours(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    ready.faults = ["after"]  # the create succeeded but the response was lost
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()  # holds the lock


def test_a_lock_replaced_by_someone_else_is_left_alone_on_release(
    ready: FakeStore, clock: Clock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ready.remove(LOCKS, LOCK_OBJECT)  # the owner ran `ops unlock`
    ready.put(LOCKS, LOCK_OBJECT, b'{"run_id": "other"}')  # and another run took it
    with caplog.at_level(logging.ERROR):
        run.release()
    assert ready.data(LOCKS, LOCK_OBJECT) == b'{"run_id": "other"}'
    assert "lock release failed" in caplog.text


def test_a_release_whose_response_was_lost_is_not_an_error(
    ready: FakeStore, clock: Clock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ready.faults = ["after"]  # deleted, but the response was lost; the retry finds no lock
    with caplog.at_level(logging.ERROR):
        run.release()
    assert ready.data(LOCKS, LOCK_OBJECT) is None
    assert caplog.text == ""


def test_the_ledger_can_only_be_opened_under_the_lock(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    with pytest.raises(LedgerStorageError, match="lock"):
        remote(ready, clock, tmp_path / "run").open()


# --- AC2: every checkpoint saves the ledger with a generation match --------------------
def test_a_checkpoint_uploads_the_ledger(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ledger = run.open()
    ledger.record_equity(NOW.date(), gbp("990"))
    run.checkpoint()
    run.release()
    check = remote(ready, clock, tmp_path / "check")
    check.acquire(LockHolder("exec-2", "run-2", "run"))
    assert check.open().previous_day_equity(NOW.date() + timedelta(days=1)) == gbp("990")


def test_a_rival_upload_is_a_lost_race(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()
    ready.put(LEDGERS, LEDGER_OBJECT, b"rival")
    with pytest.raises(LostRaceError):
        run.checkpoint()


def test_transient_failures_are_retried(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()
    ready.faults = ["before", "before"]
    run.checkpoint()
    assert ready.faults == []


def test_an_upload_that_keeps_failing_stops_the_run(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()
    ready.faults = ["before", "before", "before"]
    with pytest.raises(LedgerStorageError, match="gave up"):
        run.checkpoint()


def test_a_timed_out_upload_that_had_landed_is_not_a_lost_race(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ledger = run.open()
    ledger.record_equity(NOW.date(), gbp("990"))
    ready.faults = ["after"]  # landed; the retry gets 412 against its own upload
    run.checkpoint()
    ledger.record_equity(NOW.date() + timedelta(days=1), gbp("991"))
    run.checkpoint()  # and the next checkpoint matches the generation that landed


def test_a_timed_out_upload_beaten_by_a_rival_is_a_lost_race(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open()
    ready.faults = ["before"]

    def rival(bucket: str, name: str) -> None:
        if ready.faults == [] and bucket == LEDGERS:  # after the first, failed attempt
            ready.before_write = None
            ready.put(LEDGERS, LEDGER_OBJECT, b"rival")

    ready.before_write = rival
    with pytest.raises(LostRaceError):
        run.checkpoint()


def test_a_missing_ledger_is_never_a_fresh_start(store: FakeStore, clock: Clock, tmp_path: Path) -> None:
    run = remote(store, clock, tmp_path / "run")
    run.acquire(HOLDER)
    with pytest.raises(LedgerStorageError, match="ledger missing"):
        run.open()


def test_a_corrupt_download_is_refused(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    ready.put(LEDGERS, LEDGER_OBJECT, b"not sqlite")
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    with pytest.raises(LedgerStorageError, match="not a ledger"):
        run.open()


# --- AC3: a restored ledger can't lower the high-water mark ----------------------------
def test_setup_records_the_reference_mark(ready: FakeStore) -> None:
    assert json.loads(ready.data(LOCKS, MARK_OBJECT) or b"") == {"amount": "1000", "currency": "GBP"}


def test_a_new_peak_raises_the_reference_mark_after_the_upload(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ledger = run.open()
    ledger.update_high_water_mark(gbp("1050"), NOW)
    run.checkpoint()
    assert json.loads(ready.data(LOCKS, MARK_OBJECT) or b"") == {"amount": "1050", "currency": "GBP"}


def test_the_reference_mark_stays_when_the_upload_fails(
    ready: FakeStore, clock: Clock, tmp_path: Path
) -> None:
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    ledger = run.open()
    ledger.update_high_water_mark(gbp("1050"), NOW)
    ready.put(LEDGERS, LEDGER_OBJECT, b"rival")
    with pytest.raises(LostRaceError):
        run.checkpoint()
    assert json.loads(ready.data(LOCKS, MARK_OBJECT) or b"") == {"amount": "1000", "currency": "GBP"}


def test_a_restored_older_ledger_stops_the_run(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    old = ready.data(LEDGERS, LEDGER_OBJECT) or b""
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    run.open().update_high_water_mark(gbp("1200"), NOW)
    run.checkpoint()
    run.release()
    ready.put(LEDGERS, LEDGER_OBJECT, old)  # someone restores the setup-day version
    restored = remote(ready, clock, tmp_path / "after-restore")
    restored.acquire(LockHolder("exec-2", "run-2", "run"))
    with pytest.raises(RestoredLedgerError, match=r"1000.*1200"):
        restored.open()


def test_a_malformed_reference_mark_stops_the_run(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    ready.put(LOCKS, MARK_OBJECT, b'{"amount": "lots"}')
    run = remote(ready, clock, tmp_path / "run")
    run.acquire(HOLDER)
    with pytest.raises(LedgerStorageError, match="reference mark"):
        run.open()


def test_setup_refuses_when_a_ledger_existed_before(ready: FakeStore, clock: Clock, tmp_path: Path) -> None:
    ready.remove(LEDGERS, LEDGER_OBJECT)  # deleted, but its versions remain
    ready.remove(LOCKS, MARK_OBJECT)
    setup = remote(ready, clock, tmp_path / "again")
    setup.acquire(LockHolder("setup-exec", "setup-2", "setup"))
    with pytest.raises(SetupRefusedError, match="ledger"):
        setup.create()


def test_setup_refuses_when_a_reference_mark_exists(store: FakeStore, clock: Clock, tmp_path: Path) -> None:
    store.put(LOCKS, MARK_OBJECT, b'{"amount": "1000", "currency": "GBP"}')
    setup = remote(store, clock, tmp_path / "setup")
    setup.acquire(LockHolder("setup-exec", "setup-1", "setup"))
    with pytest.raises(SetupRefusedError, match="reference mark"):
        setup.create()


def test_setup_loses_to_a_ledger_created_meanwhile(store: FakeStore, clock: Clock, tmp_path: Path) -> None:
    setup = remote(store, clock, tmp_path / "setup")
    setup.acquire(LockHolder("setup-exec", "setup-1", "setup"))
    setup.create().seed_high_water_mark(gbp("1000"), NOW)
    store.put(LEDGERS, LEDGER_OBJECT, b"rival")
    with pytest.raises(LostRaceError):
        setup.checkpoint()
