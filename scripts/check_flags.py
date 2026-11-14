#!/usr/bin/env python3
"""Validate flags.json and its use in code (stdlib only). Run via `make flags-check`.

Errors: invalid schema, release flag without/over-long/past `expires`, kill switch not
defaulting to true, flags used in code but not registered.
Warnings: release flags expiring within 14 days, registered flags not used in code.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "flags.json"
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
TYPES = {"release", "kill-switch", "ops"}
USAGE = re.compile(r"""(?:isEnabled|is_enabled)\(\s*["']([a-z][a-z0-9-]*)["']""")
CODE_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".py"}
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "coverage", ".agentic", "stacks", "tests", "e2e"}
TEST_FILE = re.compile(r"(\.test\.[jt]sx?$|\.spec\.[jt]sx?$|^test_.*\.py$|_test\.py$)")
MAX_RELEASE_DAYS = 60


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    today = dt.date.today()

    try:
        flags = json.loads(REGISTRY.read_text(encoding="utf-8"))["flags"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: cannot read flags.json: {exc}")
        return 1

    for name, spec in flags.items():
        where = f"flags.json:{name}"
        if not NAME.match(name):
            errors.append(f"{where}: name must be kebab-case")
        kind = spec.get("type")
        if kind not in TYPES:
            errors.append(f"{where}: type must be one of {sorted(TYPES)}")
        if not isinstance(spec.get("default"), bool):
            errors.append(f"{where}: default must be true or false")
        for field in ("owner", "created", "description"):
            if not spec.get(field):
                errors.append(f"{where}: missing {field}")
        try:
            created = dt.date.fromisoformat(spec.get("created", ""))
        except ValueError:
            errors.append(f"{where}: created must be YYYY-MM-DD")
            created = today
        if kind == "kill-switch" and spec.get("default") is not True:
            errors.append(f"{where}: kill switches default to true (feature on)")
        if kind == "release":
            try:
                expires = dt.date.fromisoformat(spec.get("expires", ""))
            except ValueError:
                errors.append(f"{where}: release flags need expires: YYYY-MM-DD")
                continue
            if (expires - created).days > MAX_RELEASE_DAYS:
                errors.append(f"{where}: expires more than {MAX_RELEASE_DAYS} days after created")
            if expires < today:
                errors.append(f"{where}: expired on {expires}; remove the flag (feature-flag skill)")
            elif (expires - today).days <= 14:
                warnings.append(f"{where}: expires on {expires}")

    used: dict[str, str] = {}
    for path in ROOT.rglob("*"):
        if path.suffix not in CODE_SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts) or TEST_FILE.search(path.name):
            continue  # tests use synthetic flag names on purpose
        for match in USAGE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            used.setdefault(match.group(1), str(path.relative_to(ROOT)))

    for name, where in sorted(used.items()):
        if name not in flags:
            errors.append(f"{where}: uses unregistered flag '{name}' (add it to flags.json)")
    for name in sorted(set(flags) - set(used)):
        warnings.append(f"flags.json:{name}: not used in code")

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"flags-check: {len(flags)} flag(s), {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
