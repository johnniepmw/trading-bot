from __future__ import annotations

import io
import json

import pytest

from tradingbot.app.__main__ import main
from tradingbot.app.flags import Flags, FlagSpec

REGISTRY = {"new-orders": FlagSpec(type="kill-switch", default=True)}


def run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    out = io.StringIO()
    code = main(argv, flags=Flags(REGISTRY, env=env, trusted_env=True), version="abc123", out=out)
    return code, out.getvalue()


def test_smoke_reports_ok_version_and_kill_switch_state() -> None:
    code, out = run(["smoke"], env={})
    assert code == 0
    assert json.loads(out) == {"status": "ok", "version": "abc123", "new_orders": True}


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
def test_job_refuses_to_start_without_a_flags_bucket_when_deployed(app_env: str | None) -> None:
    out = io.StringIO()
    env = {} if app_env is None else {"APP_ENV": app_env}
    code = main(["smoke"], env=env, out=out)
    assert code == 1
    assert out.getvalue() == ""


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_job_starts_without_a_flags_bucket_in_local_and_test(app_env: str) -> None:
    out = io.StringIO()
    assert main(["smoke"], env={"APP_ENV": app_env}, version="v", out=out) == 0
    assert json.loads(out.getvalue())["new_orders"] is True


def test_deployed_job_with_a_bucket_builds_a_gcs_backed_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    # No network in unit tests: the bucket read fails, so the kill switch reports off.
    monkeypatch.setattr("tradingbot.app.flags.urllib.request.urlopen", _unreachable)
    code = main(["smoke"], env={"FLAGS_BUCKET": "b", "APP_ENV": "prod"}, version="v", out=out)
    assert code == 0
    assert json.loads(out.getvalue())["new_orders"] is False
