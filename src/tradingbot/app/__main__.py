"""Job entry point: `python -m tradingbot.app <command>`.

Phase 0 has one command, `smoke`: it proves the container starts, reads the flag
registry and reports whether the `new-orders` kill switch allows trading. The trading
run itself arrives with the strategy (Phase 1) and the Cloud Run job module (Phase 2).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TextIO

from tradingbot.app.flags import Flags, GcsSource, load_registry


def build_flags() -> Flags:
    bucket = os.environ.get("FLAGS_BUCKET")
    return Flags(load_registry(), source=GcsSource(bucket) if bucket else None)


def main(
    argv: list[str] | None = None,
    *,
    flags: Flags | None = None,
    version: str | None = None,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("command", choices=["smoke"])
    parser.parse_args(argv)

    flags = flags if flags is not None else build_flags()
    report = {
        "status": "ok",
        "version": version if version is not None else os.environ.get("APP_VERSION", "dev"),
        "new_orders": flags.is_enabled("new-orders"),
    }
    out.write(json.dumps(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
