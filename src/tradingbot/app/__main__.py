"""Job entry point: `python -m tradingbot.app <command>`.

Phase 0 has one command, `smoke`: it proves the container starts, reads the flag
registry and reports whether the `new-orders` kill switch allows trading. The trading
run itself arrives with the strategy (Phase 1) and the Cloud Run job module (Phase 2).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import TextIO

from tradingbot.app.flags import Flags, GcsSource, load_registry

log = logging.getLogger(__name__)

TRUSTED_ENVS = frozenset({"local", "test"})


class ConfigError(Exception):
    """Invalid configuration: the job refuses to start."""


def build_flags(env: Mapping[str, str]) -> Flags:
    """Flags for this process. Only APP_ENV=local|test off Cloud Run is trusted; unset means deployed."""
    on_cloud_run = bool(env.get("CLOUD_RUN_JOB") or env.get("K_SERVICE"))
    trusted = env.get("APP_ENV", "") in TRUSTED_ENVS and not on_cloud_run
    bucket = env.get("FLAGS_BUCKET")
    if not bucket and not trusted:
        raise ConfigError("FLAGS_BUCKET is required unless APP_ENV is local or test")
    return Flags(load_registry(), source=GcsSource(bucket) if bucket else None, env=env, trusted_env=trusted)


def main(
    argv: list[str] | None = None,
    *,
    flags: Flags | None = None,
    env: Mapping[str, str] | None = None,
    version: str | None = None,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("command", choices=["smoke"])
    parser.parse_args(argv)

    env = env if env is not None else os.environ
    try:
        flags = flags if flags is not None else build_flags(env)
    except ConfigError as exc:
        log.error("refusing to start: %s", exc)
        return 1
    report = {
        "status": "ok",
        "version": version if version is not None else env.get("APP_VERSION", "dev"),
        "new_orders": flags.is_enabled("new-orders"),
    }
    out.write(json.dumps(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
