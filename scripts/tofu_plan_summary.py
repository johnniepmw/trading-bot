#!/usr/bin/env python3
"""Summarise `tofu show -json <plan>` (stdin) as Markdown for a PR comment or issue.
Usage: tofu show -json plan.out | python3 scripts/tofu_plan_summary.py <env>
Highlights deletions, replacements and IAM changes, which need the owner's attention.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    env = sys.argv[1] if len(sys.argv) > 1 else "?"
    plan = json.load(sys.stdin)
    groups: dict[str, list[str]] = {"create": [], "update": [], "replace": [], "delete": []}
    iam: list[str] = []
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if actions in (["no-op"], ["read"]):
            continue
        if "delete" in actions and "create" in actions:
            kind = "replace"
        elif "delete" in actions:
            kind = "delete"
        elif "create" in actions:
            kind = "create"
        else:
            kind = "update"
        groups[kind].append(rc["address"])
        if "iam" in rc.get("type", ""):
            iam.append(f"{kind}: `{rc['address']}`")

    total = sum(len(v) for v in groups.values())
    lines = [f"### OpenTofu plan — {env}"]
    if total == 0:
        lines.append("No changes. Infrastructure matches the code.")
    else:
        lines.append(
            f"**{len(groups['create'])} to create, {len(groups['update'])} to update, "
            f"{len(groups['replace'])} to replace, {len(groups['delete'])} to delete.**"
        )
        if groups["delete"] or groups["replace"]:
            lines.append("\n:warning: **Destructive changes** (data or availability may be affected):")
            lines += [f"- {k}: `{a}`" for k in ("delete", "replace") for a in groups[k]]
        if iam:
            lines.append("\n:key: **Access (IAM) changes:**")
            lines += [f"- {x}" for x in iam]
        for kind in ("create", "update"):
            if groups[kind]:
                lines.append(f"\n<details><summary>{kind} ({len(groups[kind])})</summary>\n")
                lines += [f"- `{a}`" for a in groups[kind]]
                lines.append("\n</details>")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
