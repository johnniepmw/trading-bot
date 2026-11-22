"""Pause requests: new orders off while any request exists or the location can't be read
(Phase 2 step 7b, AC15; monitoring AC6)."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from tests.app.fake_store import FakeStore
from tests.app.test_job import RUN_DAY, SETUP_DAY, fake_orders, job_at
from tradingbot.app.__main__ import main
from tradingbot.app.flags import Flags, FlagSpec
from tradingbot.app.job import EXIT_STOPPED, JobConfigError, wiring_from
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.pause import PauseRequests
from tradingbot.app.storage import StorageError

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
