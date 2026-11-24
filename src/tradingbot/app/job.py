"""The job's commands: `run`, `run --dry-run` and the owner's `setup` (Phase 2 step 2b).

Wiring only. The config file (`config/bot.toml`) holds everything but per-environment
wiring, which comes from env vars:
- `APP_ENV`: local | test | staging | prod.
- `LEDGER_BUCKET`, `LOCK_BUCKET`: where the ledger, its lock and the reference mark live.
- `PAUSE_BUCKET`: pause requests; any object there stops new orders (step 7b).
- `LOCAL_STORE_DIR`: local and test only, a directory standing in for the buckets.
- `CLOUD_RUN_EXECUTION` (set by Cloud Run), `APP_VERSION`, `FLAGS_BUCKET` (see flags).

Local, test and staging run on fakes for good: a fake broker whose account is saved next to
the ledger, and synthetic prices. Prod uses the Trading 212 demo account (step 4) and EODHD
prices (step 3), and refuses to start while the config's instrument data is unverified. No
live broker can be built in Phase 2, whatever the `operator` secret says: the adapter
refuses any host but Trading 212's demo.
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
from tradingbot.app.daily_email import EmailError, compose, read_sender, send
from tradingbot.app.eodhd import EodhdHistory, read_key
from tradingbot.app.flags import Flags
from tradingbot.app.glance_facts import glance_input
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.operator import read_operator
from tradingbot.app.pause import PauseRequests
from tradingbot.app.remote_ledger import (
    LedgerMissingError,
    LedgerStorageError,
    LockHeldError,
    LockHolder,
    RemoteLedger,
)
from tradingbot.app.storage import GcsObjectStore, ObjectStore, metadata_token, urllib_transport
from tradingbot.brokers.fake.broker import FakeBroker
from tradingbot.brokers.port import Broker, BrokerError, read_only
from tradingbot.brokers.trading212.broker import Trading212Broker
from tradingbot.brokers.trading212.client import Trading212Client, read_credentials
from tradingbot.brokers.trading212.client import urllib_transport as broker_transport
from tradingbot.config.load import load_config
from tradingbot.config.types import BotConfig
from tradingbot.data.prices import DataError, PriceHistory
from tradingbot.data.synthetic import SyntheticHistory, synthetic_close
from tradingbot.domain.market import BENCHMARK, price_money
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import Ledger
from tradingbot.risk.limits import Limits

log = logging.getLogger(__name__)

ENVIRONMENTS = frozenset({"local", "test", "staging", "prod"})
FAKE_ENVS = frozenset({"local", "test", "staging"})
FAKE_ACCOUNT_OBJECT = "fake-broker.json"  # staging and local only: the fake broker's account
FAKE_STARTING_CASH = Money(Decimal(1000), Currency.GBP)
SETUP_EQUITY_TOLERANCE = Decimal("0.02")  # the owner's figure vs the broker's equity
EXIT_OK, EXIT_STOPPED, EXIT_SKIPPED = 0, 1, 3
CALENDAR_WARNING_DAYS = 60
RESETTABLE_ENVS = frozenset({"local", "test", "staging"})  # never prod


class JobConfigError(Exception):
    """The job can't be wired safely here: it refuses to start."""


@dataclass(frozen=True)
class Wiring:
    app_env: str
    ledger_bucket: str
    lock_bucket: str
    pause_bucket: str
    local_store_dir: Path | None
    execution: str
    version: str
    fake_now: datetime | None = None
    email_to: str | None = None  # the daily email's recipient (EMAIL_TO, set by OpenTofu)


def wiring_from(env: Mapping[str, str]) -> Wiring:
    app_env = env.get("APP_ENV", "")
    if app_env not in ENVIRONMENTS:
        raise JobConfigError(f"APP_ENV must be one of {', '.join(sorted(ENVIRONMENTS))}")
    buckets = {name: env.get(name, "") for name in ("LEDGER_BUCKET", "LOCK_BUCKET", "PAUSE_BUCKET")}
    missing = [name for name, value in buckets.items() if not value]
    if missing:
        raise JobConfigError(f"{', '.join(missing)} must be set")
    if buckets["LEDGER_BUCKET"] == buckets["LOCK_BUCKET"]:
        raise JobConfigError("the lock bucket must be separate from the ledger bucket (ADR-0017)")
    if app_env in {"staging", "prod"}:
        wrong = [name for name, value in buckets.items() if f"-{app_env}" not in value]
        if wrong:  # e.g. staging settings pointed at prod's buckets
            raise JobConfigError(f"{', '.join(wrong)} must be the {app_env} bucket (named with -{app_env})")
    if app_env in {"local", "test"} and (env.get("CLOUD_RUN_JOB") or env.get("K_SERVICE")):
        raise JobConfigError("APP_ENV local or test can't run on Cloud Run (no shared lock)")
    local = env.get("LOCAL_STORE_DIR")
    if app_env in {"local", "test"} and not local:
        raise JobConfigError("LOCAL_STORE_DIR must be set for local and test runs")
    if app_env not in {"local", "test"} and local:
        raise JobConfigError("LOCAL_STORE_DIR is for local and test runs only")
    return Wiring(
        app_env=app_env,
        ledger_bucket=buckets["LEDGER_BUCKET"],
        lock_bucket=buckets["LOCK_BUCKET"],
        pause_bucket=buckets["PAUSE_BUCKET"],
        local_store_dir=Path(local) if local else None,
        execution=env.get("CLOUD_RUN_EXECUTION") or "local",
        version=env.get("APP_VERSION", "dev"),
        fake_now=_fake_now(env, app_env),
        email_to=env.get("EMAIL_TO") or None,
    )


def _fake_now(env: Mapping[str, str], app_env: str) -> datetime | None:
    """`FAKE_NOW` fixes the clock for local and test runs (e2e on any day); refused elsewhere."""
    raw = env.get("FAKE_NOW")
    if not raw:
        return None
    if app_env not in {"local", "test"}:
        raise JobConfigError("FAKE_NOW is for local and test runs only")
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as error:
        raise JobConfigError("FAKE_NOW must be an ISO date-time in UTC") from error
    if when.utcoffset() != timedelta(0):
        raise JobConfigError("FAKE_NOW must be an ISO date-time in UTC")
    return when.astimezone(UTC)


def check_prod_ready(wiring: Wiring, config: BotConfig) -> None:
    """Prod refuses unverified instrument data (AC4)."""
    if wiring.app_env == "prod" and not config.instruments_verified:
        raise JobConfigError("prod refuses to trade: the config's instrument data is unverified")


def warn_if_live_requested(config: BotConfig, wiring: Wiring) -> None:
    """Phase 2 builds no live broker (AC8): a valid live setting is reported and ignored."""
    if wiring.app_env == "prod" and read_operator(Path(config.operator_secret_path)).live:
        log.error("operator secret asks for live trading: not available in Phase 2, staying on paper")


def build_store(wiring: Wiring, clock: Callable[[], datetime], timeout: float = 30.0) -> ObjectStore:
    if wiring.local_store_dir is not None:
        return LocalObjectStore(wiring.local_store_dir, clock)
    transport = urllib_transport(timeout=timeout)
    return GcsObjectStore(transport, token=lambda: metadata_token(transport))


def last_close_day(today: date) -> date:
    day = today - timedelta(days=1)
    while day.weekday() >= 5:  # noqa: PLR2004 - synthetic prices have weekday bars
        day -= timedelta(days=1)
    return day


class FakeAccount:
    """The fake broker's account, saved in the ledger bucket so runs share it (fakes only)."""

    def __init__(
        self, store: ObjectStore, bucket: str, config: BotConfig, clock: Callable[[], datetime]
    ) -> None:
        self._store, self._bucket, self._clock = store, bucket, clock
        today = clock().date()
        close_day = last_close_day(today)
        self._quotes = {i: price_money(i, synthetic_close(i, close_day)) for i in config.universe}
        self._generation = 0

    def load(self, *, new: bool = False) -> FakeBroker:
        """The saved account; `new` (setup only) starts one instead and refuses if one exists."""
        found = self._store.info(self._bucket, FAKE_ACCOUNT_OBJECT)
        if found is None and not new:
            raise ValueError("fake account missing: setup creates it")
        if found is not None and new:
            raise ValueError("fake account already exists: setup runs once")
        if found is None:
            broker = FakeBroker(FAKE_STARTING_CASH, self._quotes)
        else:
            self._generation = found.generation
            state = json.loads(self._store.read(self._bucket, FAKE_ACCOUNT_OBJECT, found.generation))
            broker = FakeBroker.load(state, self._quotes)
        broker.now = self._clock  # the job's clock: reconcile compares order and intent times
        return broker

    def clear(self) -> None:
        """Remove the saved fake account (a staging reset)."""
        found = self._store.info(self._bucket, FAKE_ACCOUNT_OBJECT)
        if found is not None:
            self._store.delete(self._bucket, FAKE_ACCOUNT_OBJECT, if_generation_match=found.generation)
        self._generation = 0

    def save(self, broker: FakeBroker) -> None:
        body = json.dumps(broker.dump()).encode()
        self._generation = self._store.write(
            self._bucket, FAKE_ACCOUNT_OBJECT, body, if_generation_match=self._generation
        ).generation


def _email_failed(error: Exception) -> None:
    log.warning("daily email not sent: %s: %s", type(error).__name__, error, extra={"alert": "email_failed"})


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
        return CycleConfig(
            universe=self.config.universe, limits=Limits(**vars(self.config.limits)), benchmark=BENCHMARK
        )

    def history(self) -> PriceHistory:
        """EODHD in prod (ADR-0018); synthetic prices everywhere else."""
        if self.wiring.app_env == "prod":
            return EodhdHistory(urllib_transport(timeout=30.0), read_key())
        if self.wiring.app_env not in FAKE_ENVS:
            raise JobConfigError("synthetic prices are for local, test and staging only")
        return SyntheticHistory()

    def broker(self) -> Broker:
        """Trading 212's demo account in prod (step 4); elsewhere the saved fake account, saved
        again after every order (a real broker's records are durable once it accepts one).
        Setup builds its fake account itself (`_setup_locked`)."""
        if self.wiring.app_env == "prod":
            client = Trading212Client(broker_transport(), read_credentials())
            return Trading212Broker(client, frozenset(self.config.universe), self.clock)
        account = self.fake_account()
        fake = account.load()
        fake.after_place = lambda: account.save(fake)
        return fake

    def fake_account(self) -> FakeAccount:
        if self.wiring.app_env not in FAKE_ENVS:
            raise JobConfigError("the fake broker is for local, test and staging only")
        return FakeAccount(self.store, self.wiring.ledger_bucket, self.config, self.clock)

    def _remote(self, workdir: Path) -> RemoteLedger:
        return RemoteLedger(
            self.store,
            ledger_bucket=self.wiring.ledger_bucket,
            lock_bucket=self.wiring.lock_bucket,
            workdir=workdir,
            clock=self.clock,
        )

    # Every stop ends in one log line: ERROR "cycle stopped" / "setup refused" (the alert) or
    # WARNING "dry run stopped". JobConfigError still propagates: main logs "refusing to start".
    def run(self) -> int:
        """One cycle under the lock. 0 completed, 1 stopped (alerted), 3 skipped."""
        try:
            return self._run()
        except JobConfigError:
            raise
        except Exception as error:
            log.exception("cycle stopped: %s: %s", type(error).__name__, error)
            return EXIT_STOPPED

    def dry_run(self, *, deploy_check: bool = False) -> int:
        """The cycle up to sending, on a scratch copy: no lock, no upload, no sending (AC6).
        Any stop exits 1, except with `deploy_check` (the deploy pipeline's prod check): a
        business stop then exits 0, so a deploy goes ahead while the account is deliberately
        stopped, and only an unexpected error (a bug in the image) fails it (ADR-0019)."""
        try:
            return self._dry_run(deploy_check=deploy_check)
        except JobConfigError:
            raise
        except Exception as error:  # noqa: BLE001 - reported, never an alert (the real run alerts)
            log.warning("dry run stopped: %s: %s", type(error).__name__, error)
            return EXIT_STOPPED

    def setup(self, equity: str, *, reset: bool = False) -> int:
        """The owner's ledger setup (AC9). `reset` (staging, local and test only) clears the
        ledger, reference mark and fake account first (docs/runbooks/staging-ledger-reset.md)."""
        if reset and self.wiring.app_env not in RESETTABLE_ENVS:
            raise JobConfigError("setup --reset is for staging (and local or test) only")
        try:
            return self._setup(equity, reset=reset)
        except JobConfigError:
            raise
        except Exception as error:
            log.exception("setup refused: %s: %s", type(error).__name__, error)
            return EXIT_STOPPED

    def _run(self) -> int:
        """One cycle under the lock. 0 completed, 1 stopped (alerted), 3 skipped."""
        warn_if_live_requested(self.config, self.wiring)
        check_prod_ready(self.wiring, self.config)
        now = self.clock()
        run_id = f"{self.wiring.execution}-{uuid.uuid4().hex[:12]}"  # a new ID per attempt (AC4)
        self._warn_if_calendar_ends_soon(now.date())
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
            outbox: list[tuple[str, str]] = []
            try:
                return self._run_locked(remote, run_id, signal_day, now, outbox)
            finally:
                remote.release()
                self._deliver(outbox)  # after the lock: a slow mail server can't leave it stale

    def _warn_if_calendar_ends_soon(self, today: date) -> None:
        """Weekly (Mondays) once the holiday list has under 60 days left: past it, runs stop."""
        left = (self.config.calendar_until - today).days
        if 0 <= left < CALENDAR_WARNING_DAYS and today.weekday() == 0:
            log.warning(
                "calendar ends on %s (%d days): add next year's LSE holidays to config/bot.toml",
                self.config.calendar_until.isoformat(),
                left,
                extra={"alert": "calendar_ending"},
            )

    def _run_locked(
        self,
        remote: RemoteLedger,
        run_id: str,
        signal_day: date,
        now: datetime,
        outbox: list[tuple[str, str]],
    ) -> int:
        try:
            ledger = remote.open()
        except (LedgerStorageError, OSError, ValueError) as error:
            log.error("cycle stopped: %s", error)
            return EXIT_STOPPED
        try:
            broker = self.broker()
        except (BrokerError, OSError, ValueError) as error:
            log.error("cycle stopped: %s", error)
            self._compose_daily_email(ledger, run_id, now, outbox, stopped=f"broker unavailable: {error}")
            return EXIT_STOPPED

        pause = PauseRequests(self.store, self.wiring.pause_bucket)
        if pause.present():
            log.warning(
                "paused: a pause request exists, or the pause location can't be read; no new orders",
                extra={"alert": "paused"},
            )
        report = run_cycle(
            broker=broker,
            ledger=ledger,
            history=self.history(),
            config=self._cycle_config(),
            signal_day=signal_day,
            clock=self.clock,
            run_id=run_id,
            may_send=lambda: self.flags.is_enabled("new-orders") and not pause.present(),
            checkpoint=remote.checkpoint,
            version=self.wiring.version,
        )
        if report.stopped is None:
            log.info("run completed: %d order(s) sent", report.sent)
        self._compose_daily_email(ledger, run_id, now, outbox, stopped=report.stopped)
        return EXIT_OK if report.stopped is None else EXIT_STOPPED

    def _compose_daily_email(
        self,
        ledger: Ledger,
        run_id: str,
        now: datetime,
        outbox: list[tuple[str, str]],
        *,
        stopped: str | None,
    ) -> None:
        """The glance from the ledger, while it is open (daily email spec); `_deliver` sends
        it once the lock is released. Never changes the run's outcome: any failure is a
        WARNING the alert policy mails as email_failed."""
        try:
            if not self.flags.is_enabled("daily-email"):
                return
            facts = glance_input(
                ledger,
                run_id,
                today=now.date(),
                calendar=self._calendar(),
                universe=self.config.universe,
                limits=Limits(**vars(self.config.limits)),
                now=self.clock(),
                stopped=stopped,
                in_progress=True,  # this very run: if its record has no end, it is still finishing
            )
            outbox.append(compose(facts, self.wiring.app_env))
        except Exception as error:  # noqa: BLE001 - the email must never change the run
            _email_failed(error)

    def _deliver(self, outbox: list[tuple[str, str]]) -> None:
        for subject, body in outbox:
            try:
                if self.wiring.email_to is None:
                    raise EmailError("EMAIL_TO is not set")
                send(read_sender(), self.wiring.email_to, subject, body)
            except Exception as error:  # noqa: BLE001 - the email must never change the run
                _email_failed(error)
                continue
            log.info("daily email sent")

    def _dry_run(self, *, deploy_check: bool) -> int:
        check_prod_ready(self.wiring, self.config)
        now = self.clock()
        run_id = f"{self.wiring.execution}-dry-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory() as scratch:
            try:
                calendar = self._calendar()
                signal_day = calendar.signal_day(now.date())
                reader = read_only(self.broker())
                history = self.history()
                ledger = self._remote(Path(scratch)).open_scratch()
            except (CalendarError, LedgerMissingError, BrokerError, DataError) as error:  # before ValueError
                log.warning("dry run stopped: %s", error)
                return self._business_stop(deploy_check)
            except (LedgerStorageError, OSError, ValueError) as error:  # can be a fault in this image
                log.warning("dry run stopped: %s", error)
                return EXIT_STOPPED
            try:
                report = dry_run_cycle(
                    broker=reader,
                    ledger=ledger,
                    history=history,
                    config=self._cycle_config(),
                    signal_day=signal_day,
                    clock=self.clock,
                    run_id=run_id,
                )
            finally:
                ledger.close()
        if report.stopped is None:
            return EXIT_OK
        return self._business_stop(deploy_check) if report.business else EXIT_STOPPED

    @staticmethod
    def _business_stop(deploy_check: bool) -> int:
        if deploy_check:
            log.info("deploy check: a business stop, not a fault in this image; the deploy may go ahead")
            return EXIT_OK
        return EXIT_STOPPED

    def _setup(self, equity: str, *, reset: bool) -> int:
        """The owner's ledger setup (AC9): create the ledger, seed the high-water mark from the
        broker's equity once the owner's own figure agrees, take the lock throughout."""
        check_prod_ready(self.wiring, self.config)
        try:
            stated = Money(Decimal(equity), Currency.GBP)
        except (InvalidOperation, ValueError):  # Money refuses NaN and infinity
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
                return self._setup_locked(remote, stated, now, reset=reset)
            finally:
                remote.release()

    def _setup_locked(self, remote: RemoteLedger, stated: Money, now: datetime, *, reset: bool) -> int:
        try:
            if reset:  # never prod: setup refuses before this
                remote.clear_for_reset()
                self.fake_account().clear()
                log.warning(
                    "staging ledger reset: the ledger, reference mark and fake account start afresh",
                    extra={"alert": "ledger_reset"},
                )
            account, fake = None, None
            if self.wiring.app_env == "prod":
                actual = self.broker().account().equity
            else:  # the fake account starts here, and is saved only once the ledger is
                account = self.fake_account()
                fake = account.load(new=True)
                actual = fake.account().equity
            if abs(actual.amount - stated.amount) > actual.amount * SETUP_EQUITY_TOLERANCE:
                log.error(
                    "setup refused: the broker's equity doesn't match the figure given (check the account)"
                )
                return EXIT_STOPPED
            ledger = remote.create(start_over=reset)
            ledger.seed_high_water_mark(actual, now)
            ledger.record_equity(now.date(), actual)
            remote.checkpoint()
            if account is not None and fake is not None:
                account.save(fake)
        except (LedgerStorageError, BrokerError, OSError, ValueError) as error:
            log.error("setup refused: %s", error)
            return EXIT_STOPPED
        log.info("setup completed: ledger created and high-water mark seeded")
        return EXIT_OK


def build_job(env: Mapping[str, str], flags: Flags, clock: Callable[[], datetime] | None = None) -> Job:
    wiring = wiring_from(env)
    fixed = wiring.fake_now
    now = clock or ((lambda: fixed) if fixed is not None else (lambda: datetime.now(UTC)))
    return Job(wiring=wiring, config=load_config(), store=build_store(wiring, now), flags=flags, clock=now)
