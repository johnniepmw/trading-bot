from __future__ import annotations

import json
import logging
import urllib.error
from collections.abc import Callable
from typing import Any

import pytest

from tradingbot.app.flags import (
    KILL_SWITCH_MAX_STALE_SECONDS,
    Flags,
    FlagSpec,
    GcsSource,
    env_override,
    parse_overrides,
)

REGISTRY = {
    "new-orders": FlagSpec(type="kill-switch", default=True),
    "csv-export": FlagSpec(type="release", default=False),
}
KILL = "new-orders"


class FakeSource:
    def __init__(self, *results: dict[str, bool] | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    def fetch_overrides(self) -> dict[str, bool]:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def test_env_override_parses_common_spellings() -> None:
    assert env_override("csv-export", {"FLAG_CSV_EXPORT": "on"}) is True
    assert env_override("csv-export", {"FLAG_CSV_EXPORT": " FALSE "}) is False
    assert env_override("csv-export", {"FLAG_CSV_EXPORT": "maybe"}) is None


def clock_at(value: list[float]) -> Callable[[], float]:
    return lambda: value[0]


def test_registry_defaults_apply_without_overrides() -> None:
    flags = Flags(REGISTRY, env={}, trusted_env=True)
    assert flags.is_enabled(KILL) is True
    assert flags.is_enabled("csv-export") is False


def test_unregistered_flag_raises() -> None:
    with pytest.raises(KeyError, match=r"flags\.json"):
        Flags(REGISTRY, env={}).is_enabled("nope")


def test_remote_kill_switch_turns_feature_off() -> None:
    flags = Flags(REGISTRY, env={}, source=FakeSource({KILL: False}))
    assert flags.is_enabled(KILL) is False


def test_remote_good_fetch_leaves_kill_switch_at_its_default() -> None:
    flags = Flags(REGISTRY, env={}, source=FakeSource({}))
    assert flags.is_enabled(KILL) is True


def test_refreshes_at_most_once_per_interval() -> None:
    clock = [0.0]
    source = FakeSource({})
    flags = Flags(REGISTRY, env={}, source=source, now=clock_at(clock))
    flags.is_enabled(KILL)
    flags.is_enabled(KILL)
    assert source.calls == 1
    clock[0] = 31.0
    flags.is_enabled(KILL)
    assert source.calls == 2


# AC1
def test_kill_switch_is_off_when_the_first_fetch_fails() -> None:
    # A Cloud Run job starts cold every run, so there are no last good overrides to keep.
    flags = Flags(REGISTRY, env={}, source=FakeSource(RuntimeError("storage down")))
    assert flags.is_enabled(KILL) is False


def test_kill_switch_is_off_without_a_source_unless_the_environment_is_trusted() -> None:
    assert Flags(REGISTRY, env={}).is_enabled(KILL) is False
    assert Flags(REGISTRY, env={}, trusted_env=True).is_enabled(KILL) is True


def test_kill_switch_recovers_when_a_later_fetch_succeeds() -> None:
    clock = [0.0]
    source = FakeSource(RuntimeError("storage down"), {})
    flags = Flags(REGISTRY, env={}, source=source, now=clock_at(clock))
    assert flags.is_enabled(KILL) is False
    clock[0] = 31.0
    assert flags.is_enabled(KILL) is True


def test_release_flags_keep_their_default_when_the_fetch_fails() -> None:
    flags = Flags(REGISTRY, env={}, source=FakeSource(RuntimeError("storage down")))
    assert flags.is_enabled("csv-export") is False


# AC3
def test_last_good_overrides_survive_a_failure_for_a_limited_time() -> None:
    clock = [0.0]
    source = FakeSource({}, RuntimeError("storage down"))
    flags = Flags(REGISTRY, env={}, source=source, now=clock_at(clock))
    assert flags.is_enabled(KILL) is True
    clock[0] = 31.0
    assert flags.is_enabled(KILL) is True  # failed refresh, last good is 31 s old
    clock[0] = KILL_SWITCH_MAX_STALE_SECONDS
    assert flags.is_enabled(KILL) is True  # exactly at the limit still counts
    clock[0] = KILL_SWITCH_MAX_STALE_SECONDS + 1
    assert flags.is_enabled(KILL) is False


def test_last_good_remote_off_survives_a_failure() -> None:
    clock = [0.0]
    flags = Flags(
        REGISTRY, env={}, source=FakeSource({KILL: False}, RuntimeError("down")), now=clock_at(clock)
    )
    assert flags.is_enabled(KILL) is False
    clock[0] = 31.0
    assert flags.is_enabled(KILL) is False


# AC4
@pytest.mark.parametrize("trusted", [True, False])
def test_env_off_always_turns_a_kill_switch_off(trusted: bool) -> None:
    flags = Flags(REGISTRY, env={"FLAG_NEW_ORDERS": "off"}, source=FakeSource({}), trusted_env=trusted)
    assert flags.is_enabled(KILL) is False


def test_env_on_does_not_beat_a_remote_off_when_deployed() -> None:
    flags = Flags(REGISTRY, env={"FLAG_NEW_ORDERS": "on"}, source=FakeSource({KILL: False}))
    assert flags.is_enabled(KILL) is False


def test_env_on_does_not_rescue_an_unreadable_source_when_deployed() -> None:
    flags = Flags(REGISTRY, env={"FLAG_NEW_ORDERS": "on"}, source=FakeSource(RuntimeError("down")))
    assert flags.is_enabled(KILL) is False


def test_env_on_works_in_a_trusted_local_or_test_environment() -> None:
    flags = Flags(REGISTRY, env={"FLAG_NEW_ORDERS": "on"}, source=FakeSource({KILL: False}), trusted_env=True)
    assert flags.is_enabled(KILL) is True


def test_env_still_overrides_release_flags_everywhere() -> None:
    flags = Flags(REGISTRY, env={"FLAG_CSV_EXPORT": "on"}, source=FakeSource({}))
    assert flags.is_enabled("csv-export") is True


# AC6
def test_every_fail_closed_decision_logs_an_error(caplog: pytest.LogCaptureFixture) -> None:
    flags = Flags(REGISTRY, env={}, source=FakeSource(RuntimeError("storage down")))
    with caplog.at_level(logging.WARNING, logger="tradingbot.app.flags"):
        flags.is_enabled(KILL)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [f"kill switch closed: {KILL}: no recent good flag read"]


def test_env_on_ignored_when_deployed_logs_an_error(caplog: pytest.LogCaptureFixture) -> None:
    flags = Flags(REGISTRY, env={"FLAG_NEW_ORDERS": "on"}, source=FakeSource({}))
    with caplog.at_level(logging.ERROR, logger="tradingbot.app.flags"):
        assert flags.is_enabled(KILL) is True
    assert any("env override ignored" in r.getMessage() for r in caplog.records)


# AC2: what counts as a readable source
def test_parse_overrides_accepts_bools() -> None:
    assert parse_overrides({"overrides": {"a": True, "b": False}}) == {"a": True, "b": False}
    assert parse_overrides({"overrides": {}}) == {}


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "x",
        {},
        {"overrides": None},
        {"overrides": []},
        {"overrides": {"new-orders": "off"}},
        {"overrides": {"new-orders": 0}},
    ],
)
def test_parse_overrides_rejects_anything_else(body: Any) -> None:
    with pytest.raises(ValueError, match="overrides"):
        parse_overrides(body)


def test_a_malformed_body_closes_kill_switches() -> None:
    class BadBody:
        def fetch_overrides(self) -> dict[str, bool]:
            return parse_overrides({"overrides": {"new-orders": "off"}})

    assert Flags(REGISTRY, env={}, source=BadBody()).is_enabled(KILL) is False


class _Resp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, *_: object) -> bytes:
        return json.dumps(self._payload).encode()


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, storage: Callable[[], _Resp]) -> None:
    def fake(req: Any, timeout: float = 0) -> _Resp:
        if "metadata.google.internal" in req.full_url:
            return _Resp({"access_token": "t"})
        return storage()

    monkeypatch.setattr("tradingbot.app.flags.urllib.request.urlopen", fake)


def test_gcs_source_reads_the_overrides_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, lambda: _Resp({"overrides": {"new-orders": False}}))
    assert GcsSource("b").fetch_overrides() == {"new-orders": False}


def test_gcs_source_treats_a_missing_object_as_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> _Resp:
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)  # type: ignore[arg-type]

    _patch_urlopen(monkeypatch, missing)
    with pytest.raises(urllib.error.HTTPError):
        GcsSource("b").fetch_overrides()
    flags = Flags(REGISTRY, env={}, source=GcsSource("b"))
    assert flags.is_enabled(KILL) is False
