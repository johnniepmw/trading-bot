#!/usr/bin/env python3
"""Classify a change as high or standard risk from the paths it touches (stdlib only).

Patterns come from .github/risk-paths.txt (gitignore-like globs: `**` spans directories,
`*` stays within one segment; a pattern without `/` matches at any depth).

Usage:
  python3 scripts/risk_classify.py                      # current branch vs origin/main
  python3 scripts/risk_classify.py --base main
  python3 scripts/risk_classify.py --paths a.py infra/x.tf
  python3 scripts/risk_classify.py --files-from changed.txt --author dependabot[bot]
Prints JSON: {"risk": "high"|"standard", "matches": [...], "files": N, "reason": "..."}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATTERNS = ROOT / ".github" / "risk-paths.txt"
DEPENDABOT = {"dependabot[bot]", "app/dependabot"}


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    pattern = pattern.strip().lstrip("/")
    if "/" not in pattern.rstrip("/"):
        pattern = "**/" + pattern
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def load_patterns(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            patterns.append((line, glob_to_regex(line)))
    return patterns


def classify(files: list[str], patterns: list[tuple[str, re.Pattern[str]]], author: str = "") -> dict:
    matches = []
    for f in files:
        for raw, rx in patterns:
            if rx.match(f):
                matches.append({"path": f, "pattern": raw})
                break
    if matches and author in DEPENDABOT and all(m["path"].startswith(".github/workflows/") for m in matches):
        return {
            "risk": "standard",
            "matches": matches,
            "files": len(files),
            "reason": "Dependabot version bump of GitHub Actions only",
        }
    return {
        "risk": "high" if matches else "standard",
        "matches": matches,
        "files": len(files),
        "reason": "touches high-risk paths" if matches else "no high-risk paths touched",
    }


def changed_files(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    out += [line[3:].split(" -> ")[-1] for line in status if len(line) > 3]
    return sorted(set(filter(None, out)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--paths", nargs="*")
    parser.add_argument("--files-from")
    parser.add_argument("--author", default="")
    parser.add_argument("--patterns", default=str(DEFAULT_PATTERNS))
    args = parser.parse_args()

    if args.paths is not None:
        files = args.paths
    elif args.files_from:
        files = [f.strip() for f in Path(args.files_from).read_text().splitlines() if f.strip()]
    else:
        files = changed_files(args.base)

    result = classify(files, load_patterns(Path(args.patterns)), args.author)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
