from __future__ import annotations

import pytest

from app.flags import Flags, FlagSpec, env_override

REGISTRY = {
    "home-greeting": FlagSpec(type="kill-switch", default=True),
    "csv-export": FlagSpec(type="release", default=False),
}


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


def test_registry_defaults_apply_without_overrides() -> None:
    flags = Flags(REGISTRY, env={})
    assert flags.is_enabled("home-greeting") is True
    assert flags.is_enabled("csv-export") is False


def test_unregistered_flag_raises() -> None:
    with pytest.raises(KeyError, match=r"flags\.json"):
        Flags(REGISTRY, env={}).is_enabled("nope")


def test_remote_kill_switch_turns_feature_off() -> None:
    flags = Flags(REGISTRY, env={}, source=FakeSource({"home-greeting": False}))
    assert flags.is_enabled("home-greeting") is False


def test_env_wins_over_remote() -> None:
    flags = Flags(REGISTRY, env={"FLAG_HOME_GREETING": "true"}, source=FakeSource({"home-greeting": False}))
    assert flags.is_enabled("home-greeting") is True


def test_last_good_overrides_survive_source_failure() -> None:
    clock = [0.0]
    flags = Flags(
        REGISTRY,
        env={},
        source=FakeSource({"home-greeting": False}, RuntimeError("storage down")),
        now=lambda: clock[0],
    )
    assert flags.is_enabled("home-greeting") is False
    clock[0] = 31.0
    assert flags.is_enabled("home-greeting") is False


def test_refreshes_at_most_once_per_interval() -> None:
    clock = [0.0]
    source = FakeSource({})
    flags = Flags(REGISTRY, env={}, source=source, now=lambda: clock[0])
    flags.is_enabled("home-greeting")
    flags.is_enabled("home-greeting")
    assert source.calls == 1
    clock[0] = 31.0
    flags.is_enabled("home-greeting")
    assert source.calls == 2
