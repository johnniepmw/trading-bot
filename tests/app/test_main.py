from __future__ import annotations

import io
import json
import logging

import pytest

from tradingbot.app.__main__ import main
from tradingbot.app.flags import Flags, FlagSpec
from tradingbot.app.pause import PauseRequests

REGISTRY = {"new-orders": FlagSpec(type="kill-switch", default=True)}


def run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    out = io.StringIO()
    flags = Flags(REGISTRY, env=env, trusted_env=True)
    code = main(argv, flags=flags, version="abc123", out=out, pause=PauseRequests(None, None))
    return code, out.getvalue()


def test_smoke_reports_ok_version_and_kill_switch_state() -> None:
    code, out = run(["smoke"], env={})
    assert code == 0
    assert json.loads(out) == {"status": "ok", "version": "abc123", "new_orders": True, "paused": False}


def test_smoke_shows_the_kill_switch_when_it_is_off() -> None:
    _, out = run(["smoke"], env={"FLAG_NEW_ORDERS": "off"})
    assert json.loads(out)["new_orders"] is False


def test_unknown_command_is_refused() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["trade-now"], env={})
    assert exc.value.code == 2


def _unreachable(*_: object, **__: object) -> None:
    raise OSError("no network in unit tests")


@pytest.mark.parametrize("app_env", [None, "", "prod", "production", "staging"])
def test_job_refuses_to_start_without_a_flags_bucket_when_deployed(
    app_env: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    out = io.StringIO()
    env = {} if app_env is None else {"APP_ENV": app_env}
    with caplog.at_level(logging.ERROR):
        code = main(["smoke"], env=env, out=out)
    assert code == 1
    assert out.getvalue() == ""
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("refusing to start:")]


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_job_starts_without_a_flags_bucket_in_local_and_test(app_env: str) -> None:
    out = io.StringIO()
    assert main(["smoke"], env={"APP_ENV": app_env}, version="v", out=out) == 0
    assert json.loads(out.getvalue())["new_orders"] is True


@pytest.mark.parametrize("marker", ["CLOUD_RUN_JOB", "K_SERVICE"])
def test_app_env_local_is_not_trusted_on_cloud_run(marker: str) -> None:
    out = io.StringIO()
    code = main(["smoke"], env={"APP_ENV": "local", marker: "x"}, version="v", out=out)
    assert code == 1


def test_deployed_job_with_a_bucket_builds_a_gcs_backed_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    # No network in unit tests: the bucket read fails, so the kill switch reports off.
    monkeypatch.setattr("tradingbot.app.storage._OPENER.open", _unreachable)
    env = {"FLAGS_BUCKET": "b", "PAUSE_BUCKET": "p", "APP_ENV": "prod"}
    no_pause = PauseRequests(None, None)  # the kill switch alone decides here
    code = main(["smoke"], env=env, version="v", out=out, pause=no_pause)
    assert code == 0
    assert json.loads(out.getvalue())["new_orders"] is False


def test_smoke_also_logs_a_readable_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        run(["smoke"], env={})
    assert "smoke: new_orders=True paused=False version=abc123" in caplog.text


def test_setup_accepts_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool]] = []

    class FakeJob:
        def setup(self, equity: str, *, reset: bool = False) -> int:
            seen.append((equity, reset))
            return 0

    monkeypatch.setattr("tradingbot.app.__main__.build_job", lambda env, flags: FakeJob())
    assert run(["setup", "--equity", "1000", "--reset"], env={})[0] == 0
    assert seen == [("1000", True)]


def test_run_passes_the_deploy_check_to_the_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    class FakeJob:
        def dry_run(self, *, deploy_check: bool = False) -> int:
            seen.append(deploy_check)
            return 0

    monkeypatch.setattr("tradingbot.app.__main__.build_job", lambda env, flags: FakeJob())
    assert run(["run", "--dry-run", "--deploy-check"], env={})[0] == 0
    assert seen == [True]


def test_the_deploy_check_needs_a_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tradingbot.app.__main__.build_job", lambda env, flags: object())
    with pytest.raises(SystemExit) as exc:
        run(["run", "--deploy-check"], env={})
    assert exc.value.code == 2
