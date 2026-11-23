"""Job entry point: `python -m tradingbot.app <command>`.

- `smoke`: the container starts, reads the flag registry and the pause location, and
  reports `paused` and whether new orders are allowed (the kill switch and no pause).
- `run`: one daily cycle (`app/job.py`); `run --dry-run` stops before sending, on a
  scratch copy of the ledger.
- `setup --equity <GBP>`: the owner's one-off ledger setup (docs/runbooks/ledger-setup.md).

Logs are JSON lines on stderr with a `severity` field, as Cloud Logging reads them.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from tradingbot.app.alerts import alert_kind
from tradingbot.app.flags import Flags, GcsSource, load_registry
from tradingbot.app.job import EXIT_STOPPED, JobConfigError, build_job
from tradingbot.app.local_store import LocalObjectStore
from tradingbot.app.pause import PauseRequests
from tradingbot.app.storage import GcsObjectStore, metadata_token, urllib_transport
from tradingbot.config.load import ConfigFileError

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


def build_pause(env: Mapping[str, str]) -> PauseRequests:
    """The pause check for `smoke`. Deployed environments must name their pause location."""
    on_cloud_run = bool(env.get("CLOUD_RUN_JOB") or env.get("K_SERVICE"))
    trusted = env.get("APP_ENV", "") in TRUSTED_ENVS and not on_cloud_run
    bucket = env.get("PAUSE_BUCKET")
    if not bucket:
        if not trusted:
            raise ConfigError("PAUSE_BUCKET is required unless APP_ENV is local or test")
        return PauseRequests(None, None)
    local = env.get("LOCAL_STORE_DIR")
    if local and trusted:
        return PauseRequests(LocalObjectStore(Path(local)), bucket)
    transport = urllib_transport(timeout=10.0)
    return PauseRequests(GcsObjectStore(transport, token=lambda: metadata_token(transport)), bucket)


def main(  # noqa: PLR0913 - injectable dependencies for tests
    argv: list[str] | None = None,
    *,
    flags: Flags | None = None,
    env: Mapping[str, str] | None = None,
    version: str | None = None,
    out: TextIO = sys.stdout,
    pause: PauseRequests | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("smoke")
    run = commands.add_parser("run")
    run.add_argument("--dry-run", action="store_true", help="stop before sending; touch nothing")
    setup = commands.add_parser("setup")
    setup.add_argument("--equity", required=True, help="the account's equity in GBP, as you see it")
    args = parser.parse_args(argv)

    env = env if env is not None else os.environ
    try:
        flags = flags if flags is not None else build_flags(env)
    except ConfigError as exc:
        log.error("refusing to start: %s", exc)
        return 1
    if args.command != "smoke":
        try:
            job = build_job(env, flags)
            if args.command == "setup":
                return job.setup(args.equity)
            return job.dry_run() if args.dry_run else job.run()
        except (JobConfigError, ConfigFileError) as exc:
            log.error("refusing to start: %s", exc)
            return EXIT_STOPPED
    try:
        pause = pause if pause is not None else build_pause(env)
    except ConfigError as exc:
        log.error("refusing to start: %s", exc)
        return 1
    paused = pause.present()
    report = {
        "status": "ok",
        "version": version if version is not None else env.get("APP_VERSION", "dev"),
        "new_orders": flags.is_enabled("new-orders") and not paused,
        "paused": paused,
    }
    out.write(json.dumps(report) + "\n")
    log.info(  # the same, as a message `make logs` shows
        "smoke: new_orders=%s paused=%s version=%s", report["new_orders"], report["paused"], report["version"]
    )
    return 0


class JsonFormatter(logging.Formatter):
    """One JSON object per line; Cloud Logging takes `severity` and `message` from it."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        kind = alert_kind(record)
        if kind is not None:
            entry["alert"] = kind  # what Cloud Monitoring's alert policy matches
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)  # stdout is the smoke report's
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


if __name__ == "__main__":
    configure_logging()
    try:
        code = main()
    except Exception as error:
        log.exception("crashed: %s", type(error).__name__)
        code = 1
    sys.exit(code)
