"""The ledger in object storage: one run at a time, every checkpoint saved (ADR-0017).

A run (or the owner's setup) first takes a create-only lock object in the lock bucket, then
downloads the ledger to a scratch file and works on it locally. `checkpoint` uploads the
file with a generation match, so a rival writer is detected rather than overwritten; the
executor calls it before every order is sent and after every change of an intent's state.
After a successful upload the reference mark (the highest high-water mark ever saved, kept
outside the ledger) is raised; a downloaded ledger whose mark is below it was restored from
an older version and is refused. The lock is released last. A held lock stops the run
quietly (a duplicate trigger); a stale one stops it with an alert, and only the owner clears
it (`ops unlock`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypeVar

from tradingbot.app.storage import (
    ObjectInfo,
    ObjectStore,
    PreconditionFailedError,
    StorageError,
    TransientStorageError,
)
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import Ledger, LedgerError

log = logging.getLogger(__name__)

LEDGER_OBJECT = "ledger.sqlite"
LOCK_OBJECT = "run.lock"
MARK_OBJECT = "reference-mark.json"
STALE_AFTER = timedelta(hours=1)  # well past the job's timeout: the holder can't still be running
ATTEMPTS = 3
_T = TypeVar("_T")


class LedgerStorageError(Exception):
    """The remote ledger can't be used safely: no new orders."""


class LockHeldError(LedgerStorageError):
    """Another run holds the lock (a duplicate trigger, or a run still going)."""


class StaleLockError(LedgerStorageError):
    """A lock older than STALE_AFTER: a run died holding it. The owner clears it."""


class LostRaceError(LedgerStorageError):
    """Someone else wrote the object since this run read it."""


class RestoredLedgerError(LedgerStorageError):
    """The ledger's high-water mark is below the reference mark: an older version was restored."""


class SetupRefusedError(LedgerStorageError):
    """Setup would start a ledger where one existed before."""


@dataclass(frozen=True, slots=True)
class LockHolder:
    execution: str  # the Cloud Run execution name
    run_id: str
    holder: str  # what holds it: "run", "setup"


def _md5(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


class RemoteLedger:
    def __init__(  # noqa: PLR0913 - explicit wiring
        self,
        store: ObjectStore,
        *,
        ledger_bucket: str,
        lock_bucket: str,
        workdir: Path,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._ledger_bucket = ledger_bucket
        self._lock_bucket = lock_bucket
        self._path = workdir / LEDGER_OBJECT
        self._clock = clock
        self._sleep = sleep
        self._lock: ObjectInfo | None = None
        self._scratch = False
        self._ledger: Ledger | None = None
        self._generation = 0  # of the ledger object this run last read or wrote
        self._mark: Money | None = None
        self._mark_generation = 0

    # --- retries ---------------------------------------------------------------------
    def _retry(self, what: str, call: Callable[[], _T]) -> _T:
        for attempt in range(ATTEMPTS):
            try:
                return call()
            except TransientStorageError as error:
                if attempt == ATTEMPTS - 1:
                    raise LedgerStorageError(f"{what}: gave up after {ATTEMPTS} attempts: {error}") from error
                self._sleep(2.0**attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def _write(self, bucket: str, name: str, data: bytes, expected: int) -> ObjectInfo:
        """Write with a generation match. A 412 after an attempt that may have landed is
        checked against the stored object's MD5: our own upload is success, anything else a
        lost race."""
        uncertain = False

        def attempt() -> ObjectInfo:
            nonlocal uncertain
            try:
                return self._store.write(bucket, name, data, if_generation_match=expected)
            except TransientStorageError:
                uncertain = True
                raise

        try:
            return self._retry(f"write {name}", attempt)
        except PreconditionFailedError as error:
            if uncertain:
                stored = self._retry(f"info {name}", lambda: self._store.info(bucket, name))
                if stored is not None and stored.md5 == _md5(data):
                    return stored
            raise LostRaceError(f"{name} changed since this run read it: another writer") from error

    # --- the lock (AC1) --------------------------------------------------------------
    def acquire(self, holder: LockHolder) -> None:
        body = json.dumps(asdict(holder), sort_keys=True).encode()
        try:
            self._lock = self._write(self._lock_bucket, LOCK_OBJECT, body, expected=0)
        except LostRaceError:
            self._refuse(holder)

    def _refuse(self, holder: LockHolder) -> None:
        held = self._retry("lock info", lambda: self._store.info(self._lock_bucket, LOCK_OBJECT))
        if held is None:  # released between our write and this read: still not ours
            log.warning(
                "run skipped: lock held by another run (released since); %s not started", holder.run_id
            )
            raise LockHeldError("the lock was held")
        who = self._describe(held)
        if self._clock() - held.created > STALE_AFTER:
            log.error(
                "stale lock: held by %s since %s; the owner clears it with ops unlock",
                who,
                held.created.isoformat(),
            )
            raise StaleLockError(f"stale lock held by {who}")
        log.warning("run skipped: lock held by %s since %s", who, held.created.isoformat())
        raise LockHeldError(f"lock held by {who}")

    def _describe(self, held: ObjectInfo) -> str:
        try:
            raw = json.loads(self._store.read(self._lock_bucket, LOCK_OBJECT, held.generation))
            return f"{raw['execution']} (run {raw['run_id']})"
        except (StorageError, ValueError, KeyError, TypeError):
            return "an unreadable holder"

    def release(self) -> None:
        """Close the local ledger and delete our lock (by generation, so never someone
        else's). Never raises: a failure is logged as an alert and the lock goes stale."""
        try:
            if self._ledger is not None:
                ledger, self._ledger = self._ledger, None
                ledger.close()
        except Exception as error:  # noqa: BLE001 - still release the lock
            log.error("ledger close failed: %s", error)
        lock, self._lock = self._lock, None
        if lock is None:
            return
        try:
            self._delete_lock(lock)
        except Exception as error:  # noqa: BLE001 - never raises; the lock goes stale and alerts
            log.error("lock release failed: %s", error)

    def _delete_lock(self, lock: ObjectInfo) -> None:
        uncertain = False

        def attempt() -> None:
            nonlocal uncertain
            try:
                self._store.delete(self._lock_bucket, LOCK_OBJECT, if_generation_match=lock.generation)
            except TransientStorageError:
                uncertain = True
                raise

        try:
            self._retry("release lock", attempt)
        except PreconditionFailedError:
            gone = self._retry("lock info", lambda: self._store.info(self._lock_bucket, LOCK_OBJECT)) is None
            if not (uncertain and gone):  # gone after a lost response: our delete landed
                raise LedgerStorageError("the lock was replaced or removed by someone else") from None

    # --- the ledger (AC2, AC3) -------------------------------------------------------
    def _require_lock(self) -> None:
        if self._lock is None:
            raise LedgerStorageError("take the lock before opening the ledger")

    def open(self) -> Ledger:
        """Download the ledger and check it against the reference mark."""
        self._require_lock()
        return self._download()

    def open_scratch(self) -> Ledger:
        """A dry run's copy (Phase 2 AC6): no lock, and `checkpoint` refuses to upload it."""
        self._scratch = True
        return self._download()

    def _download(self) -> Ledger:
        found = self._retry("ledger info", lambda: self._store.info(self._ledger_bucket, LEDGER_OBJECT))
        if found is None:
            raise LedgerStorageError("ledger missing: it is created only by the owner's setup")
        data = self._retry(
            "ledger read", lambda: self._store.read(self._ledger_bucket, LEDGER_OBJECT, found.generation)
        )
        self._path.write_bytes(data)
        try:
            ledger = Ledger.open(self._path)
        except (LedgerError, sqlite3.DatabaseError) as error:
            raise LedgerStorageError(f"not a ledger: {error}") from error
        self._read_mark()
        stored = ledger.high_water_mark()
        if self._mark is None:  # only setup creates it; recreating it is an owner step
            ledger.close()
            log.log(
                logging.WARNING if self._scratch else logging.ERROR,  # a dry run alerts nothing
                "reference mark missing: the run stops; restore it (ledger restore runbook)",
            )
            raise RestoredLedgerError("reference mark missing: a run never re-creates it from the ledger")
        if stored is None or stored < self._mark:
            ledger.close()
            raise RestoredLedgerError(
                f"the ledger's high-water mark ({stored.amount if stored else 'none'}) is below the reference"
                f" mark ({self._mark.amount}): an older ledger was restored"
            )
        self._ledger, self._generation = ledger, found.generation
        return ledger

    def create(self) -> Ledger:
        """First-time setup only: refuses if a ledger ever existed or a reference mark does."""
        self._require_lock()
        if self._retry(
            "ledger versions", lambda: self._store.has_any_version(self._ledger_bucket, LEDGER_OBJECT)
        ):
            raise SetupRefusedError(
                "a ledger existed before (see its versions): restore it, don't start afresh"
            )
        if self._retry("mark info", lambda: self._store.info(self._lock_bucket, MARK_OBJECT)) is not None:
            raise SetupRefusedError("a reference mark exists: a ledger existed before")
        self._ledger, self._generation = Ledger.create(self._path), 0
        return self._ledger

    def checkpoint(self) -> None:
        """Upload the ledger (generation match), then raise the reference mark if needed."""
        if self._ledger is None:
            raise LedgerStorageError("no ledger open")
        if self._scratch:
            raise LedgerStorageError("a dry run's scratch copy is never uploaded")
        saved = self._write(self._ledger_bucket, LEDGER_OBJECT, self._path.read_bytes(), self._generation)
        self._generation = saved.generation
        mark = self._ledger.high_water_mark()
        if mark is not None and (self._mark is None or mark > self._mark):
            body = json.dumps({"amount": str(mark.amount), "currency": mark.currency.value}).encode()
            self._mark_generation = self._write(
                self._lock_bucket, MARK_OBJECT, body, self._mark_generation
            ).generation
            self._mark = mark

    def _read_mark(self) -> None:
        found = self._retry("mark info", lambda: self._store.info(self._lock_bucket, MARK_OBJECT))
        if found is None:
            self._mark, self._mark_generation = None, 0
            return
        data = self._retry(
            "mark read", lambda: self._store.read(self._lock_bucket, MARK_OBJECT, found.generation)
        )
        try:
            raw = json.loads(data)
            mark = Money(Decimal(raw["amount"]), Currency(raw["currency"]))
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            raise LedgerStorageError(f"reference mark unreadable ({type(error).__name__})") from error
        self._mark, self._mark_generation = mark, found.generation
