"""The job's commands on fakes and a local store (Phase 2 step 2b: AC4, AC6 to AC9)."""

from __future__ import annotations

import ast
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.app.__main__ import build_flags
from tradingbot.app.job import (
    EXIT_OK,
    EXIT_SKIPPED,
    EXIT_STOPPED,
    FAKE_ACCOUNT_OBJECT,
    Job,
    JobConfigError,
    build_job,
    check_prod_ready,
    wiring_from,
)
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.remote_ledger import LEDGER_OBJECT, LOCK_OBJECT
from tradingbot.config.load import load_config
from tradingbot.ledger.store import IntentState, Ledger

SETUP_DAY = datetime(2026, 11, 30, 7, 15, tzinfo=UTC)  # a Monday
RUN_DAY = datetime(2026, 12, 1, 7, 15, tzinfo=UTC)  # the first run after November's month end
SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot"


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "LEDGER_BUCKET": "ledger",
        "LOCK_BUCKET": "locks",
        "PAUSE_BUCKET": "pause",
        "LOCAL_STORE_DIR": str(tmp_path / "store"),
        "FLAG_NEW_ORDERS": "on",
        **extra,
    }


def job_at(tmp_path: Path, when: datetime, **extra: str) -> Job:
    env = env_for(tmp_path, **extra)
    return build_job(env, build_flags(env), clock=lambda: when)


def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "store", clock=lambda: RUN_DAY)


def fake_orders(tmp_path: Path) -> list[dict[str, str]]:
    s = store(tmp_path)
    info = s.info("ledger", FAKE_ACCOUNT_OBJECT)
    assert info is not None
    orders: list[dict[str, str]] = json.loads(s.read("ledger", FAKE_ACCOUNT_OBJECT, info.generation))[
        "orders"
    ]
    return orders


@pytest.fixture
def set_up(tmp_path: Path) -> Path:
    assert job_at(tmp_path, SETUP_DAY).setup("1000") == EXIT_OK
    return tmp_path


# --- wiring (AC4) ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"APP_ENV": "production"}, "APP_ENV"),
        ({"LEDGER_BUCKET": ""}, "LEDGER_BUCKET"),
        ({"LOCK_BUCKET": "ledger"}, "separate"),
        ({"LOCAL_STORE_DIR": ""}, "LOCAL_STORE_DIR"),
        (
            {
                "APP_ENV": "staging",
                "LEDGER_BUCKET": "p-staging-l",
                "LOCK_BUCKET": "p-staging-k",
                "PAUSE_BUCKET": "p-staging-p",
            },
            "local and test runs only",
        ),  # a directory store outside local/test
    ],
)
def test_bad_wiring_refuses_to_start(tmp_path: Path, change: dict[str, str], message: str) -> None:
    with pytest.raises(JobConfigError, match=message):
        wiring_from(env_for(tmp_path, **change))


def test_prod_refuses_unverified_instruments(tmp_path: Path) -> None:
    wiring = wiring_from(
        {
            "APP_ENV": "prod",
            "LEDGER_BUCKET": "p-prod-ledger",
            "LOCK_BUCKET": "p-prod-locks",
            "PAUSE_BUCKET": "p-prod-pause",
        }
    )
    with pytest.raises(JobConfigError, match="unverified"):
        check_prod_ready(wiring, load_config())


def test_prod_refuses_fakes_even_with_verified_instruments(tmp_path: Path) -> None:
    from dataclasses import replace  # noqa: PLC0415 - this test only

    wiring = wiring_from(
        {
            "APP_ENV": "prod",
            "LEDGER_BUCKET": "p-prod-ledger",
            "LOCK_BUCKET": "p-prod-locks",
            "PAUSE_BUCKET": "p-prod-pause",
        }
    )
    with pytest.raises(JobConfigError, match="Trading 212 adapter"):
        check_prod_ready(wiring, replace(load_config(), instruments_verified=True))


def test_a_live_operator_secret_builds_nothing_live(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from dataclasses import replace  # noqa: PLC0415 - this test only

    secret = tmp_path / "operator"
    secret.write_text('{"mode": "live", "capital_gbp": "500"}')
    env = {
        "APP_ENV": "prod",
        "LEDGER_BUCKET": "p-prod-ledger",
        "LOCK_BUCKET": "p-prod-locks",
        "PAUSE_BUCKET": "p-prod-pause",
        "FLAG_NEW_ORDERS": "off",
    }
    job = Job(
        wiring=wiring_from(env),
        config=replace(load_config(), operator_secret_path=str(secret)),
        store=store(tmp_path),
        flags=build_flags({"APP_ENV": "test"}),
        clock=lambda: RUN_DAY,
    )
    with pytest.raises(JobConfigError), caplog.at_level(logging.ERROR):
        job.run()
    assert "not available in Phase 2" in caplog.text


def test_nothing_imports_a_live_broker() -> None:
    sources = "\n".join(p.read_text() for p in SRC.rglob("*.py"))
    assert "brokers.trading212" not in sources  # the adapter arrives in step 4, demo only


# --- setup (AC9) ----------------------------------------------------------------------
def test_setup_creates_the_ledger_and_seeds_the_mark(set_up: Path) -> None:
    s = store(set_up)
    assert s.info("ledger", LEDGER_OBJECT) is not None
    assert s.info("locks", LOCK_OBJECT) is None  # released


def test_setup_refuses_an_equity_that_doesnt_match_the_broker(tmp_path: Path) -> None:
    assert job_at(tmp_path, SETUP_DAY).setup("1200") == EXIT_STOPPED
    assert store(tmp_path).info("ledger", LEDGER_OBJECT) is None


def test_setup_refuses_a_second_time(set_up: Path) -> None:
    assert job_at(set_up, SETUP_DAY).setup("1000") == EXIT_STOPPED


def test_setup_refuses_an_amount_that_isnt_one(tmp_path: Path) -> None:
    assert job_at(tmp_path, SETUP_DAY).setup("lots") == EXIT_STOPPED


# --- run (AC4, AC7) --------------------------------------------------------------------
def test_a_run_without_a_ledger_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        assert job_at(tmp_path, RUN_DAY).run() == EXIT_STOPPED
    assert "ledger missing" in caplog.text


def test_a_month_end_run_sends_orders_and_saves_everything(
    set_up: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert job_at(set_up, RUN_DAY).run() == EXIT_OK
    sent = fake_orders(set_up)
    assert sent  # the synthetic prices put some funds in trend
    assert "run completed" in caplog.text
    assert store(set_up).info("locks", LOCK_OBJECT) is None


def test_a_second_run_the_same_day_sends_nothing_more(set_up: Path) -> None:
    job_at(set_up, RUN_DAY).run()
    first = len(fake_orders(set_up))
    assert job_at(set_up, RUN_DAY).run() == EXIT_OK  # a new run ID per attempt
    assert len(fake_orders(set_up)) == first


def test_the_kill_switch_stops_sending(set_up: Path) -> None:
    assert job_at(set_up, RUN_DAY, FLAG_NEW_ORDERS="off").run() == EXIT_STOPPED
    assert fake_orders(set_up) == []


def test_a_held_lock_skips_the_run(set_up: Path, caplog: pytest.LogCaptureFixture) -> None:
    store(set_up).write(
        "locks", LOCK_OBJECT, b'{"execution": "x", "run_id": "y", "holder": "run"}', if_generation_match=0
    )
    with caplog.at_level(logging.WARNING):
        assert job_at(set_up, RUN_DAY).run() == EXIT_SKIPPED
    assert "run skipped" in caplog.text
    assert fake_orders(set_up) == []


def test_a_non_trading_day_does_nothing(set_up: Path) -> None:
    christmas = datetime(2026, 12, 25, 7, 15, tzinfo=UTC)
    assert job_at(set_up, christmas).run() == EXIT_OK
    assert fake_orders(set_up) == []


def test_a_date_past_the_calendar_stops_the_run(set_up: Path) -> None:
    assert job_at(set_up, datetime(2028, 1, 4, 7, 15, tzinfo=UTC)).run() == EXIT_STOPPED


def saved_ledger(tmp_path: Path) -> Ledger:
    s = store(tmp_path)
    info = s.info("ledger", LEDGER_OBJECT)
    assert info is not None
    path = tmp_path / "check.sqlite"
    path.write_bytes(s.read("ledger", LEDGER_OBJECT, info.generation))
    return Ledger.open(path)


class Crash(BaseException):
    """The process dies mid-send."""


def test_drill_a_crash_mid_send_then_a_fresh_run_sends_nothing_twice(
    set_up: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradingbot.app.remote_ledger import RemoteLedger  # noqa: PLC0415 - patched below

    original = RemoteLedger.checkpoint
    calls = 0

    def crash_after_the_first_send(self: RemoteLedger) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:  # after reconcile, before order 1, then here: order 1 sent, not yet saved
            raise Crash
        original(self)

    monkeypatch.setattr(RemoteLedger, "checkpoint", crash_after_the_first_send)
    with pytest.raises(Crash):
        job_at(set_up, RUN_DAY).run()
    monkeypatch.setattr(RemoteLedger, "checkpoint", original)
    assert len(fake_orders(set_up)) == 1  # order 1 reached the broker; the remote ledger says intended

    first_order = fake_orders(set_up)[0]["id"]

    assert job_at(set_up, RUN_DAY).run() == EXIT_OK  # a fresh run with a new run ID
    tickers = [o["ticker"] for o in fake_orders(set_up)]
    assert len(tickers) > 1  # the rest of the month end was sent
    assert len(tickers) == len(set(tickers))  # and nothing twice
    ledger = saved_ledger(set_up)
    [matched] = [i for i in ledger.settled() if i.broker_order_id == first_order]
    assert matched.state is IntentState.FILLED  # reconcile linked the saved intent to its broker order
    # (a real crash would also leave the lock held until `ops unlock`; `release` runs here)


# --- dry run (AC6) ----------------------------------------------------------------------
def test_a_dry_run_reports_and_touches_nothing(set_up: Path, caplog: pytest.LogCaptureFixture) -> None:
    before = {p: p.read_bytes() for p in (set_up / "store").rglob("*") if p.is_file()}
    with caplog.at_level(logging.INFO):
        assert job_at(set_up, RUN_DAY).dry_run() == EXIT_OK
    after = {p: p.read_bytes() for p in (set_up / "store").rglob("*") if p.is_file()}
    assert after == before  # no upload, no lock, nothing rewritten
    assert fake_orders(set_up) == []
    assert "order(s) would be sent" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_dry_run_that_stops_exits_non_zero_without_cycle_stopped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert job_at(tmp_path, RUN_DAY).dry_run() == EXIT_STOPPED  # no ledger
    assert "dry run stopped" in caplog.text
    assert "cycle stopped" not in caplog.text


def test_a_dry_run_ignores_a_held_lock(set_up: Path) -> None:
    store(set_up).write("locks", LOCK_OBJECT, b"{}", if_generation_match=0)
    assert job_at(set_up, RUN_DAY).dry_run() == EXIT_OK


@pytest.mark.parametrize(
    ("path", "name"), [("cycle.py", "dry_run_cycle"), ("cycle.py", "_decide"), ("job.py", "dry_run")]
)
def test_the_dry_run_path_cant_reach_sending(path: str, name: str) -> None:
    # type-level: dry_run_cycle and _decide take a BrokerReader, which has no `place` (mypy);
    # and none of these functions mentions execute, place, run_cycle or checkpoint
    tree = ast.parse((SRC / "app" / path).read_text())
    [func] = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == name]
    used = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)
    }
    assert not used & {"execute", "place", "run_cycle", "checkpoint"}


# --- the fakes the job runs on --------------------------------------------------------
def test_the_fake_account_survives_a_round_trip() -> None:
    from tradingbot.brokers.fake.broker import FakeBroker  # noqa: PLC0415
    from tradingbot.domain.approval import ApprovedOrder  # noqa: PLC0415
    from tradingbot.domain.market import Instrument, Order, Side  # noqa: PLC0415
    from tradingbot.domain.money import Currency, Money  # noqa: PLC0415

    vwrp = Instrument("VWRP", Currency.GBP)
    quotes = {vwrp: Money(Decimal(100), Currency.GBP)}
    broker = FakeBroker(Money(Decimal(1000), Currency.GBP), quotes)
    broker.now = lambda: RUN_DAY
    broker.place(ApprovedOrder(Order(vwrp, Side.BUY, Decimal("2.5"))))
    again = FakeBroker.load(json.loads(json.dumps(broker.dump())), quotes)
    assert (again.account(), again.positions(), again.recent_orders()) == (
        broker.account(),
        broker.positions(),
        broker.recent_orders(),
    )


def test_synthetic_prices_pass_the_history_checks() -> None:
    from datetime import date  # noqa: PLC0415

    from tradingbot.data.prices import validate_history  # noqa: PLC0415
    from tradingbot.data.synthetic import SyntheticHistory  # noqa: PLC0415

    history = SyntheticHistory()
    universe = load_config().universe
    checked = [
        validate_history(i, history.daily_bars(i, date(2025, 1, 1), date(2026, 11, 30))) for i in universe
    ]
    assert all(len(bars) > 400 for bars in checked)


def test_the_local_store_keeps_generations_and_versions(tmp_path: Path) -> None:
    from tradingbot.app.storage import PreconditionFailedError  # noqa: PLC0415

    s = store(tmp_path)
    first = s.write("b", "o", b"one", if_generation_match=0)
    with pytest.raises(PreconditionFailedError):
        s.write("b", "o", b"two", if_generation_match=0)
    second = s.write("b", "o", b"two", if_generation_match=first.generation)
    s.delete("b", "o", if_generation_match=second.generation)
    assert s.info("b", "o") is None
    assert s.has_any_version("b", "o")
    assert s.read("b", "o", first.generation) == b"one"


def test_a_dry_run_reports_account_vetoes_without_alerting(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert job_at(tmp_path, RUN_DAY).setup("1000") == EXIT_OK  # seeded today: no day-start equity yet
    with caplog.at_level(logging.WARNING):
        job_at(tmp_path, RUN_DAY).dry_run()
    assert "no day-start equity" in caplog.text  # a run would alert on this
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class BrokenStore(LocalObjectStore):
    """A bucket the job's identity can't use (HTTP 403)."""

    def info(self, bucket: str, name: str) -> None:  # type: ignore[override]
        from tradingbot.app.storage import StorageError  # noqa: PLC0415

        raise StorageError(f"info {name}: HTTP 403")


def broken_job(tmp_path: Path, when: datetime) -> Job:
    from dataclasses import replace  # noqa: PLC0415

    return replace(job_at(tmp_path, when), store=BrokenStore(tmp_path / "store"))


def test_a_storage_failure_stops_the_run_with_an_alert(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert broken_job(tmp_path, RUN_DAY).run() == EXIT_STOPPED
    assert any(
        r.getMessage().startswith("cycle stopped: ") and "403" in r.getMessage() for r in caplog.records
    )


def test_a_storage_failure_stops_setup_with_an_alert(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert broken_job(tmp_path, SETUP_DAY).setup("1000") == EXIT_STOPPED
    assert "setup refused" in caplog.text


def test_a_storage_failure_stops_a_dry_run_with_its_own_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert broken_job(tmp_path, RUN_DAY).dry_run() == EXIT_STOPPED
    assert "dry run stopped" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("equity", ["NaN", "Infinity", "-5"])
def test_setup_refuses_an_equity_that_isnt_a_positive_amount(tmp_path: Path, equity: str) -> None:
    assert job_at(tmp_path, SETUP_DAY).setup(equity) == EXIT_STOPPED
    assert store(tmp_path).info("ledger", LEDGER_OBJECT) is None


# --- hardening from the security review ------------------------------------------------
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"APP_ENV": "staging", "LOCAL_STORE_DIR": "", "LEDGER_BUCKET": "p-prod-ledger"}, "staging"),
        (
            {
                "APP_ENV": "staging",
                "LOCAL_STORE_DIR": "",
                "LOCK_BUCKET": "p-prod-locks",
                "LEDGER_BUCKET": "p-staging-l",
            },
            "staging",
        ),
        ({"CLOUD_RUN_JOB": "trading-bot-prod"}, "Cloud Run"),  # a directory store on Cloud Run
    ],
)
def test_wiring_binds_buckets_and_stores_to_the_environment(
    tmp_path: Path, change: dict[str, str], message: str
) -> None:
    with pytest.raises(JobConfigError, match=message):
        wiring_from(env_for(tmp_path, **change))


def test_staging_buckets_named_for_staging_are_accepted(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        APP_ENV="staging",
        LOCAL_STORE_DIR="",
        LEDGER_BUCKET="p-staging-ledger",
        LOCK_BUCKET="p-staging-locks",
        PAUSE_BUCKET="p-staging-pause",
    )
    assert wiring_from(env).app_env == "staging"


def test_fakes_are_never_built_for_prod(tmp_path: Path) -> None:
    from dataclasses import replace  # noqa: PLC0415

    job = job_at(tmp_path, RUN_DAY)
    prod = replace(job, wiring=replace(job.wiring, app_env="prod"))
    with pytest.raises(JobConfigError, match="fake"):
        prod.fake_account()
    with pytest.raises(JobConfigError, match="synthetic"):
        prod.history()


def test_a_run_refuses_a_missing_fake_account(set_up: Path, caplog: pytest.LogCaptureFixture) -> None:
    store(set_up).delete(
        "ledger",
        FAKE_ACCOUNT_OBJECT,
        if_generation_match=store(set_up).info("ledger", FAKE_ACCOUNT_OBJECT).generation,
    )  # type: ignore[union-attr]
    with caplog.at_level(logging.ERROR):
        assert job_at(set_up, RUN_DAY).run() == EXIT_STOPPED
    assert "fake account missing" in caplog.text


def test_the_dry_runs_broker_has_no_place_at_run_time() -> None:
    from tradingbot.brokers.fake.broker import FakeBroker  # noqa: PLC0415
    from tradingbot.brokers.port import read_only  # noqa: PLC0415
    from tradingbot.domain.money import Currency, Money  # noqa: PLC0415

    reader = read_only(FakeBroker(Money(Decimal(1000), Currency.GBP), {}))
    assert not hasattr(reader, "place")
    assert reader.account().cash == Money(Decimal(1000), Currency.GBP)
