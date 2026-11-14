#!/usr/bin/env python3
"""Docs hygiene checks (stdlib only). Run via `make docs-check`.

Errors (exit 1):
  - AGENTS.md over 150 lines, CLAUDE.md over 60 lines
  - broken relative links in Markdown files
  - ADRs without a Status line, or missing from docs/adr/README.md
Warnings:
  - active exec plans whose date prefix is more than 14 days old
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", ".venv", ".terraform", "dist", "coverage", ".agentic"}
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
LIMITS = {"AGENTS.md": 150, "CLAUDE.md": 60}

errors: list[str] = []
warnings: list[str] = []


def markdown_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        out.append(path)
    return sorted(out)


def check_limits() -> None:
    for name, limit in LIMITS.items():
        path = ROOT / name
        if path.exists():
            n = len(path.read_text(encoding="utf-8").splitlines())
            if n > limit:
                errors.append(f"{name} has {n} lines (limit {limit}); move detail into docs/")


def check_links(path: Path) -> None:
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in LINK.findall(line):
            if target.startswith(("http://", "https://", "mailto:", "#")) or "{{" in target:
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                rel = path.relative_to(ROOT)
                errors.append(f"{rel}:{lineno}: broken link -> {target}")


def check_adrs() -> None:
    adr_dir = ROOT / "docs" / "adr"
    if not adr_dir.exists():
        return
    index_path = adr_dir / "README.md"
    index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    for adr in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if adr.name.startswith("0000-"):
            continue
        text = adr.read_text(encoding="utf-8")
        if not re.search(r"^- \*\*Status\*\*: *\S", text, re.MULTILINE):
            errors.append(f"docs/adr/{adr.name}: missing '- **Status**:' line")
        if adr.name not in index:
            errors.append(f"docs/adr/{adr.name}: not listed in docs/adr/README.md")


def check_stale_plans() -> None:
    today = dt.date.today()
    for plan in sorted((ROOT / "docs" / "exec-plans" / "active").glob("*.md")):
        match = re.match(r"(\d{4}-\d{2}-\d{2})-", plan.name)
        if not match:
            warnings.append(f"{plan.relative_to(ROOT)}: name should start with YYYY-MM-DD-")
            continue
        age = (today - dt.date.fromisoformat(match.group(1))).days
        if age > 14:
            warnings.append(f"{plan.relative_to(ROOT)}: active for {age} days (finish, abandon or escalate)")


def main() -> int:
    check_limits()
    for path in markdown_files():
        check_links(path)
    check_adrs()
    check_stale_plans()
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"docs-check: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
