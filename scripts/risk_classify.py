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
# A changed line Dependabot may make in a workflow: a SHA-pinned `uses:` with its version
# comment. Groups: the action (owner/repo[/path]), which must not change; the SHA; the tag.
# risk_gate.py also checks that the tag resolves to the SHA in owner/repo (no fork commits).
PINNED_USES = re.compile(
    r"^[+-]\s*(?:-\s+)?uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@([0-9a-f]{40})\s+#\s*(v\d+(?:\.\d+)*)\s*$"
)


def added_pins(patch: str | None) -> list[tuple[str, str, str]]:
    """(action, sha, tag) for each added pinned `uses:` line of a patch."""
    pins = []
    for line in (patch or "").splitlines():
        m = PINNED_USES.match(line)
        if m and line.startswith("+"):
            pins.append((m.group(1), m.group(2), m.group(3)))
    return pins


def is_pin_bump(patch: str | None) -> bool:
    """True if every added or removed line of a unified diff hunk is a pinned `uses:` line,
    and the same actions (in order) are removed as added. A missing or empty patch is False."""
    if not patch:
        return False
    removed, added = [], []
    for line in patch.splitlines():
        if not line.startswith(("+", "-")):
            continue
        m = PINNED_USES.match(line)
        if not m:
            return False
        (added if line.startswith("+") else removed).append(m.group(1))
    return bool(added) and removed == added


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


def classify(
    files: list[str],
    patterns: list[tuple[str, re.Pattern[str]]],
    author: str = "",
    patches: dict[str, str | None] | None = None,
) -> dict:
    """`patches` maps a path to its unified-diff patch (from the GitHub PR files API). The
    Dependabot exemption needs one for every matched workflow file; without it, high risk."""
    matches = []
    for f in files:
        for raw, rx in patterns:
            if rx.match(f):
                matches.append({"path": f, "pattern": raw})
                break
    patches = patches or {}
    if (
        matches
        and author in DEPENDABOT
        and all(
            m["path"].startswith(".github/workflows/") and is_pin_bump(patches.get(m["path"]))
            for m in matches
        )
    ):
        return {
            "risk": "standard",
            "matches": matches,
            "files": len(files),
            "reason": "Dependabot SHA-pin bump of GitHub Actions only",
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
