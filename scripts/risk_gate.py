#!/usr/bin/env python3
"""risk-gate: sets the required `risk-gate` commit status on a PR's head commit.

Runs in .github/workflows/risk-gate.yml from the DEFAULT branch only (pull_request_target
and issue_comment), so a PR cannot change the rules that judge it. Never checks out or
executes PR code; reads PR metadata through the GitHub API.

standard risk                         -> success
high risk + owner `/approve-risk <sha>` comment matching the current head -> success
high risk otherwise                   -> pending (+ one explanatory comment per head SHA)

Env: GH_TOKEN, GITHUB_REPOSITORY, PR, APPROVER (owner login), GITHUB_SERVER_URL, GITHUB_RUN_ID
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from risk_classify import DEFAULT_PATTERNS, classify, load_patterns

CONTEXT = "risk-gate"
APPROVE = re.compile(r"^\s*/approve-risk\s+([0-9a-f]{7,40})\b", re.IGNORECASE | re.MULTILINE)
MARKER = "<!-- risk-gate:{sha} -->"


def gh_api(path: str, *args: str, paginate: bool = False) -> object:
    cmd = ["gh", "api", path, *args]
    if paginate:
        cmd += ["--paginate", "--slurp"]  # one JSON array of pages
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return [item for page in json.loads(out or "[]") for item in page]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        proc.check_returncode()
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def set_status(repo: str, sha: str, state: str, description: str) -> None:
    target = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    gh_api(
        f"repos/{repo}/statuses/{sha}",
        "-X",
        "POST",
        "-f",
        f"state={state}",
        "-f",
        f"context={CONTEXT}",
        "-f",
        f"description={description[:139]}",
        "-f",
        f"target_url={target}",
    )
    print(f"{CONTEXT}: {state} — {description}")


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR"]
    approver = os.environ.get("APPROVER", "").lower()

    pr = gh_api(f"repos/{repo}/pulls/{pr_number}")
    sha = pr["head"]["sha"]
    author = pr["user"]["login"]
    if pr.get("state") != "open":
        print("PR is not open; nothing to do")
        return 0

    files = []
    for f in gh_api(f"repos/{repo}/pulls/{pr_number}/files", paginate=True):
        files.append(f["filename"])
        if f.get("previous_filename"):
            files.append(f["previous_filename"])

    result = classify(files, load_patterns(DEFAULT_PATTERNS), author)
    if result["risk"] == "standard":
        set_status(repo, sha, "success", f"Standard risk: {result['reason']}")
        return 0

    comments = gh_api(f"repos/{repo}/issues/{pr_number}/comments", paginate=True)
    for c in comments:
        if c["user"]["login"].lower() != approver:
            continue
        for approved in APPROVE.findall(c.get("body") or ""):
            if sha.startswith(approved.lower()):
                set_status(repo, sha, "success", f"High risk, approved by {approver} for {sha[:7]}")
                return 0

    set_status(repo, sha, "pending", f"High risk: owner must comment /approve-risk {sha[:7]}")
    marker = MARKER.format(sha=sha)
    if not any(marker in (c.get("body") or "") for c in comments):
        paths = "\n".join(f"- `{m['path']}` (matches `{m['pattern']}`)" for m in result["matches"][:20])
        more = "" if len(result["matches"]) <= 20 else f"\n- …and {len(result['matches']) - 20} more"
        body = (
            f"{marker}\n### Owner approval needed (risk-gate)\n"
            f"This PR touches high-risk paths:\n{paths}{more}\n\n"
            "Read the **Owner decision needed** section of the PR description. "
            f"To approve this exact commit, reply with:\n\n`/approve-risk {sha[:7]}`\n\n"
            "Any new push needs a fresh approval. Once approved, auto-merge (if armed) completes "
            "the merge; otherwise tap Squash and merge."
        )
        gh_api(f"repos/{repo}/issues/{pr_number}/comments", "-X", "POST", "-f", f"body={body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
