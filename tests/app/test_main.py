from __future__ import annotations

import io
import json

import pytest

from tradingbot.app.__main__ import main
from tradingbot.app.flags import Flags, FlagSpec

REGISTRY = {"new-orders": FlagSpec(type="kill-switch", default=True)}


def run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    out = io.StringIO()
    code = main(argv, flags=Flags(REGISTRY, env=env), version="abc123", out=out)
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
