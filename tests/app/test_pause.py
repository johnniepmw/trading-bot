"""Pause requests: new orders off while any request exists or the location can't be read
(Phase 2 step 7b, AC15; monitoring AC6)."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.app.fake_store import FakeStore
from tests.app.test_job import RUN_DAY, SETUP_DAY, fake_orders, job_at
from tradingbot.app.__main__ import main
from tradingbot.app.flags import Flags, FlagSpec
from tradingbot.app.job import EXIT_STOPPED, JobConfigError, wiring_from
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.pause import PauseRequestError, PauseRequests, request_pause
from tradingbot.app.storage import ObjectInfo, PreconditionFailedError, StorageError, TransientStorageError

REGISTRY = {"new-orders": FlagSpec(type="kill-switch", default=True)}


class Unreadable(FakeStore):
    def has_objects(self, bucket: str) -> bool:
        raise StorageError("list pause: HTTP 403")


@pytest.fixture
def store() -> FakeStore:
    return FakeStore(lambda: RUN_DAY)


def test_no_request_means_not_paused(store: FakeStore) -> None:
    assert PauseRequests(store, "pause").present() is False


def test_any_request_pauses(store: FakeStore) -> None:
    store.put("pause", "requests/2026-11-23T07:00:00Z", b"{}")
    assert PauseRequests(store, "pause").present() is True


def test_a_cleared_request_no_longer_pauses(store: FakeStore) -> None:
    store.put("pause", "requests/x", b"{}")
    store.remove("pause", "requests/x")
    assert PauseRequests(store, "pause").present() is False


def test_an_unreadable_location_pauses_with_an_alert(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        assert PauseRequests(Unreadable(lambda: RUN_DAY), "pause").present() is True
    [line] = [r for r in caplog.records if "pause location unreadable" in r.getMessage()]
    assert getattr(line, "alert", None) == "pause_unreadable"


def test_no_location_in_local_runs_means_not_paused() -> None:
    assert PauseRequests(None, None).present() is False


# --- the job ---------------------------------------------------------------------------
def test_a_paused_run_sends_nothing_and_alerts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert job_at(tmp_path, SETUP_DAY).setup("1000") == 0
    LocalObjectStore(tmp_path / "store").write("pause", "requests/owner", b"{}", if_generation_match=0)
    with caplog.at_level(logging.WARNING):
        assert job_at(tmp_path, RUN_DAY).run() == EXIT_STOPPED
    assert fake_orders(tmp_path) == []
    assert [getattr(r, "alert", None) for r in caplog.records if r.getMessage().startswith("paused")] == [
        "paused"
    ]


def test_the_job_needs_a_pause_location(tmp_path: Path) -> None:
    env = {"APP_ENV": "test", "LEDGER_BUCKET": "l", "LOCK_BUCKET": "k", "LOCAL_STORE_DIR": str(tmp_path)}
    with pytest.raises(JobConfigError, match="PAUSE_BUCKET"):
        wiring_from(env)


def test_a_staging_pause_location_must_be_stagings(tmp_path: Path) -> None:
    env = {
        "APP_ENV": "staging",
        "LEDGER_BUCKET": "p-ledger-staging",
        "LOCK_BUCKET": "p-locks-staging",
        "PAUSE_BUCKET": "p-pause-prod",
    }
    with pytest.raises(JobConfigError, match="PAUSE_BUCKET"):
        wiring_from(env)


# --- smoke --------------------------------------------------------------------------------
def smoke(env: dict[str, str], pause: PauseRequests | None = None) -> dict[str, object]:
    out = io.StringIO()
    flags = Flags(REGISTRY, env=env, trusted_env=True)
    assert main(["smoke"], flags=flags, env=env, version="v", out=out, pause=pause) == 0
    report: dict[str, object] = json.loads(out.getvalue())
    return report


def test_smoke_reports_a_pause(store: FakeStore) -> None:
    store.put("pause", "requests/x", b"{}")
    assert smoke({}, PauseRequests(store, "pause")) | {"version": "v"} == {
        "status": "ok",
        "version": "v",
        "new_orders": False,
        "paused": True,
    }


def test_smoke_without_a_pause(store: FakeStore) -> None:
    report = smoke({}, PauseRequests(store, "pause"))
    assert (report["new_orders"], report["paused"]) == (True, False)


def test_a_deployed_smoke_needs_a_pause_location(caplog: pytest.LogCaptureFixture) -> None:
    out = io.StringIO()
    flags = Flags(REGISTRY, env={}, trusted_env=False)
    with caplog.at_level(logging.ERROR):
        code = main(
            ["smoke"], flags=flags, env={"APP_ENV": "prod", "FLAGS_BUCKET": "f"}, version="v", out=out
        )
    assert code == 1
    assert "PAUSE_BUCKET" in caplog.text


def test_the_local_store_ignores_cleared_requests(tmp_path: Path) -> None:
    local = LocalObjectStore(tmp_path / "store")
    written = local.write("pause", "requests/x", b"{}", if_generation_match=0)
    assert PauseRequests(local, "pause").present() is True
    local.delete("pause", "requests/x", if_generation_match=written.generation)
    assert PauseRequests(local, "pause").present() is False


# --- writing a pause request (the dashboard's button) ----------------------------------
NOW_UTC = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)


class Flaky(LocalObjectStore):
    """Fails the first `fail` writes; `landed` makes the first failure land anyway."""

    def __init__(
        self, root: Path, fail: int, *, landed: bool = False, error: Exception | None = None
    ) -> None:
        super().__init__(root)
        self.fail, self.landed, self.error, self.writes = fail, landed, error, 0

    def write(self, bucket: str, name: str, data: bytes, *, if_generation_match: int) -> ObjectInfo:
        self.writes += 1
        if self.fail:
            self.fail -= 1
            if self.landed:
                self.landed = False
                super().write(bucket, name, data, if_generation_match=if_generation_match)
            raise self.error or TransientStorageError("timeout")
        return super().write(bucket, name, data, if_generation_match=if_generation_match)


def test_a_pause_request_is_one_new_object_and_an_alert(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = LocalObjectStore(tmp_path)
    with caplog.at_level(logging.WARNING):
        name = request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)
    assert name.startswith("dashboard-20261124T093000Z-")
    assert store.has_objects("pause")
    (record,) = [r for r in caplog.records if r.getMessage().startswith("pause requested")]
    assert getattr(record, "alert", None) == "pause_requested"


def test_two_presses_make_two_requests(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    assert request_pause(store, "pause", NOW_UTC) != request_pause(store, "pause", NOW_UTC)


def test_a_timeout_is_retried(tmp_path: Path) -> None:
    store = Flaky(tmp_path, fail=1)
    request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)
    assert (store.writes, store.has_objects("pause")) == (2, True)


def test_a_retry_that_finds_its_own_write_is_success(tmp_path: Path) -> None:
    store = Flaky(tmp_path, fail=1, landed=True)
    request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)
    assert store.has_objects("pause")


def test_storage_down_is_an_error_not_a_pause(tmp_path: Path) -> None:
    store = Flaky(tmp_path, fail=3)
    with pytest.raises(PauseRequestError, match="unavailable"):
        request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)
    assert not store.has_objects("pause")


def test_a_refused_write_is_an_error(tmp_path: Path) -> None:
    store = Flaky(tmp_path, fail=1, error=StorageError("403"))
    with pytest.raises(PauseRequestError, match="refused"):
        request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)


def test_a_name_clash_without_an_earlier_attempt_is_an_error(tmp_path: Path) -> None:
    store = Flaky(tmp_path, fail=1, error=PreconditionFailedError("412"))
    with pytest.raises(PauseRequestError, match="already exists"):
        request_pause(store, "pause", NOW_UTC, sleep=lambda _: None)
