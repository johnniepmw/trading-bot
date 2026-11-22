"""The job's commands: `run`, `run --dry-run` and the owner's `setup` (Phase 2 step 2b).

Wiring only. The config file (`config/bot.toml`) holds everything but per-environment
wiring, which comes from env vars:
- `APP_ENV`: local | test | staging | prod.
- `LEDGER_BUCKET`, `LOCK_BUCKET`: where the ledger, its lock and the reference mark live.
- `LOCAL_STORE_DIR`: local and test only, a directory standing in for the buckets.
- `CLOUD_RUN_EXECUTION` (set by Cloud Run), `APP_VERSION`, `FLAGS_BUCKET` (see flags).

Local, test and staging run on fakes for good: a fake broker whose account is saved next to
the ledger, and synthetic prices. Prod refuses to start until the Trading 212 adapter (step 4)
and the live price source (step 3) exist, and while the config's instrument data is
unverified. No live broker can be built in Phase 2, whatever the `operator` secret says.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tradingbot.app.calendar import CalendarError, TradingCalendar
from tradingbot.app.cycle import CycleConfig, dry_run_cycle, run_cycle
from tradingbot.app.flags import Flags
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.operator import read_operator
from tradingbot.app.remote_ledger import (
    LedgerStorageError,
    LockHeldError,
    LockHolder,
    RemoteLedger,
)
from tradingbot.app.storage import GcsObjectStore, ObjectStore, metadata_token, urllib_transport
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.config.load import load_config
from tradingbot.config.types import BotConfig
from tradingbot.data.prices import PriceHistory
from tradingbot.data.synthetic import SyntheticHistory, synthetic_close
from tradingbot.domain.market import price_money
from tradingbot.domain.money import Currency, Money
from tradingbot.risk.limits import Limits

log = logging.getLogger(__name__)

ENVIRONMENTS = frozenset({"local", "test", "staging", "prod"})
FAKE_ENVS = frozenset({"local", "test", "staging"})
FAKE_ACCOUNT_OBJECT = "fake-broker.json"  # staging and local only: the fake broker's account
FAKE_STARTING_CASH = Money(Decimal(1000), Currency.GBP)
SETUP_EQUITY_TOLERANCE = Decimal("0.02")  # the owner's figure vs the broker's equity
EXIT_OK, EXIT_STOPPED, EXIT_SKIPPED = 0, 1, 3


class JobConfigError(Exception):
    """The job can't be wired safely here: it refuses to start."""


@dataclass(frozen=True)
class Wiring:
    app_env: str
    ledger_bucket: str
    lock_bucket: str
    local_store_dir: Path | None
    execution: str
    version: str


def wiring_from(env: Mapping[str, str]) -> Wiring:
    app_env = env.get("APP_ENV", "")
    if app_env not in ENVIRONMENTS:
        raise JobConfigError(f"APP_ENV must be one of {', '.join(sorted(ENVIRONMENTS))}")
    buckets = {name: env.get(name, "") for name in ("LEDGER_BUCKET", "LOCK_BUCKET")}
    missing = [name for name, value in buckets.items() if not value]
    if missing:
        raise JobConfigError(f"{', '.join(missing)} must be set")
    if buckets["LEDGER_BUCKET"] == buckets["LOCK_BUCKET"]:
        raise JobConfigError("the lock bucket must be separate from the ledger bucket (ADR-0017)")
    local = env.get("LOCAL_STORE_DIR")
    if app_env in {"local", "test"} and not local:
        raise JobConfigError("LOCAL_STORE_DIR must be set for local and test runs")
    if app_env not in {"local", "test"} and local:
        raise JobConfigError("LOCAL_STORE_DIR is for local and test runs only")
    return Wiring(
        app_env=app_env,
        ledger_bucket=buckets["LEDGER_BUCKET"],
        lock_bucket=buckets["LOCK_BUCKET"],
        local_store_dir=Path(local) if local else None,
        execution=env.get("CLOUD_RUN_EXECUTION") or "local",
        version=env.get("APP_VERSION", "dev"),
    )


def check_prod_ready(wiring: Wiring, config: BotConfig) -> None:
    """Prod refuses fakes, unverified instruments and anything not built yet (AC4)."""
    if wiring.app_env != "prod":
        return
    if not config.instruments_verified:
        raise JobConfigError("prod refuses to trade: the config's instrument data is unverified")
    raise JobConfigError("prod needs the Trading 212 adapter (step 4) and a live price source (step 3)")


def warn_if_live_requested(config: BotConfig, wiring: Wiring) -> None:
    """Phase 2 builds no live broker (AC8): a valid live setting is reported and ignored."""
    if wiring.app_env == "prod" and read_operator(Path(config.operator_secret_path)).live:
        log.error("operator secret asks for live trading: not available in Phase 2, staying on paper")


def build_store(wiring: Wiring, clock: Callable[[], datetime]) -> ObjectStore:
    if wiring.local_store_dir is not None:
        return LocalObjectStore(wiring.local_store_dir, clock)
    transport = urllib_transport(timeout=30.0)
    return GcsObjectStore(transport, token=lambda: metadata_token(transport))


def last_close_day(today: date) -> date:
    day = today - timedelta(days=1)
    while day.weekday() >= 5:  # noqa: PLR2004 - synthetic prices have weekday bars
        day -= timedelta(days=1)
    return day


class FakeAccount:
    """The fake broker's account, saved in the ledger bucket so runs share it (fakes only)."""

    def __init__(self, store: ObjectStore, bucket: str, config: BotConfig, today: date) -> None:
        self._store, self._bucket = store, bucket
        close_day = last_close_day(today)
        self._quotes = {i: price_money(i, synthetic_close(i, close_day)) for i in config.universe}
        self._generation = 0

    def load(self) -> FakeBroker:
        found = self._store.info(self._bucket, FAKE_ACCOUNT_OBJECT)
        if found is None:
            return FakeBroker(FAKE_STARTING_CASH, self._quotes)
        self._generation = found.generation
        state = json.loads(self._store.read(self._bucket, FAKE_ACCOUNT_OBJECT, found.generation))
        return FakeBroker.load(state, self._quotes)

    def save(self, broker: FakeBroker) -> None:
        body = json.dumps(broker.dump()).encode()
        self._generation = self._store.write(
            self._bucket, FAKE_ACCOUNT_OBJECT, body, if_generation_match=self._generation
        ).generation


@dataclass(frozen=True)
class Job:
    wiring: Wiring
    config: BotConfig
    store: ObjectStore
    flags: Flags
    clock: Callable[[], datetime]

    def _calendar(self) -> TradingCalendar:
        return TradingCalendar(self.config.holidays, until=self.config.calendar_until)

    def _cycle_config(self) -> CycleConfig:
        return CycleConfig(universe=self.config.universe, limits=Limits(**vars(self.config.limits)))

    def _history(self) -> PriceHistory:
        return SyntheticHistory()  # prod never gets here (check_prod_ready)

    def _remote(self, workdir: Path) -> RemoteLedger:
        return RemoteLedger(
            self.store,
            ledger_bucket=self.wiring.ledger_bucket,
            lock_bucket=self.wiring.lock_bucket,
            workdir=workdir,
            clock=self.clock,
        )

    def run(self) -> int:
        """One cycle under the lock. 0 completed, 1 stopped (alerted), 3 skipped."""
        warn_if_live_requested(self.config, self.wiring)
        check_prod_ready(self.wiring, self.config)
        now = self.clock()
        run_id = f"{self.wiring.execution}-{uuid.uuid4().hex[:12]}"  # a new ID per attempt (AC4)
        try:
            if not self._calendar().is_trading_day(now.date()):
                log.info("not a trading day: nothing to do")
                return EXIT_OK
            signal_day = self._calendar().signal_day(now.date())
        except CalendarError as error:
            log.error("cycle stopped: %s", error)
            return EXIT_STOPPED
        with tempfile.TemporaryDirectory() as scratch:
            remote = self._remote(Path(scratch))
            try:
                remote.acquire(LockHolder(self.wiring.execution, run_id, "run"))
            except LockHeldError:
                return EXIT_SKIPPED  # logged by acquire: a duplicate trigger, not an alert
            except LedgerStorageError as error:
                log.error("cycle stopped: %s", error)
                return EXIT_STOPPED
            try:
                return self._run_locked(remote, run_id, signal_day, now)
            finally:
                remote.release()

    def _run_locked(self, remote: RemoteLedger, run_id: str, signal_day: date, now: datetime) -> int:
        try:
            ledger = remote.open()
            account = FakeAccount(self.store, self.wiring.ledger_bucket, self.config, now.date())
            broker = account.load()
        except (LedgerStorageError, OSError, ValueError) as error:
            log.error("cycle stopped: %s", error)
            return EXIT_STOPPED
        # a real broker's records are durable the moment it accepts an order; so is the fake's
        broker.after_place = lambda: account.save(broker)

        report = run_cycle(
            broker=broker,
            ledger=ledger,
            history=self._history(),
            config=self._cycle_config(),
            signal_day=signal_day,
            clock=self.clock,
            run_id=run_id,
            may_send=lambda: self.flags.is_enabled("new-orders"),
            checkpoint=remote.checkpoint,
            version=self.wiring.version,
        )
        if report.stopped is None:
            log.info("run completed: %d order(s) sent", report.sent)
            return EXIT_OK
        return EXIT_STOPPED

    def dry_run(self) -> int:
        """The cycle up to sending, on a scratch copy: no lock, no upload, no sending (AC6)."""
        check_prod_ready(self.wiring, self.config)
        now = self.clock()
        run_id = f"{self.wiring.execution}-dry-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory() as scratch:
            try:
                calendar = self._calendar()
                signal_day = calendar.signal_day(now.date())
                ledger = self._remote(Path(scratch)).open_scratch()
                reader = FakeAccount(self.store, self.wiring.ledger_bucket, self.config, now.date()).load()
            except (CalendarError, LedgerStorageError, OSError, ValueError) as error:
                log.warning("dry run stopped: %s", error)
                return EXIT_STOPPED
            try:
                report = dry_run_cycle(
                    broker=reader,
                    ledger=ledger,
                    history=self._history(),
                    config=self._cycle_config(),
                    signal_day=signal_day,
                    clock=self.clock,
                    run_id=run_id,
                )
            finally:
                ledger.close()
        return EXIT_OK if report.stopped is None else EXIT_STOPPED

    def setup(self, equity: str) -> int:
        """The owner's ledger setup (AC9): create the ledger, seed the high-water mark from the
        broker's equity once the owner's own figure agrees, take the lock throughout."""
        check_prod_ready(self.wiring, self.config)
        try:
            stated = Money(Decimal(equity), Currency.GBP)
        except InvalidOperation:
            log.error("setup refused: --equity must be an amount in GBP")
            return EXIT_STOPPED
        now = self.clock()
        run_id = f"{self.wiring.execution}-setup-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory() as scratch:
            remote = self._remote(Path(scratch))
            try:
                remote.acquire(LockHolder(self.wiring.execution, run_id, "setup"))
            except LedgerStorageError as error:
                log.error("setup refused: %s", error)
                return EXIT_STOPPED
            try:
                return self._setup_locked(remote, stated, now)
            finally:
                remote.release()

    def _setup_locked(self, remote: RemoteLedger, stated: Money, now: datetime) -> int:
        account = FakeAccount(self.store, self.wiring.ledger_bucket, self.config, now.date())
        try:
            broker = account.load()
            actual = broker.account().equity
            if abs(actual.amount - stated.amount) > actual.amount * SETUP_EQUITY_TOLERANCE:
                log.error(
                    "setup refused: the broker's equity doesn't match the figure given (check the account)"
                )
                return EXIT_STOPPED
            ledger = remote.create()
            ledger.seed_high_water_mark(actual, now)
            ledger.record_equity(now.date(), actual)
            remote.checkpoint()
            account.save(broker)
        except (LedgerStorageError, OSError, ValueError) as error:
            log.error("setup refused: %s", error)
            return EXIT_STOPPED
        log.info("setup completed: ledger created and high-water mark seeded")
        return EXIT_OK


def build_job(env: Mapping[str, str], flags: Flags, clock: Callable[[], datetime] | None = None) -> Job:
    wiring = wiring_from(env)
    now = clock or (lambda: datetime.now(UTC))
    return Job(wiring=wiring, config=load_config(), store=build_store(wiring, now), flags=flags, clock=now)
