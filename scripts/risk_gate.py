#!/usr/bin/env python3
"""risk-gate: sets the required `risk-gate` commit status on a PR's head commit.

Runs in .github/workflows/risk-gate.yml from the DEFAULT branch only (pull_request_target
and issue_comment), so a PR cannot change the rules that judge it. Never checks out or
executes PR code; reads PR metadata through the GitHub API.

standard risk                         -> success
  (a Dependabot workflow bump must also pin each action to the SHA its version tag names)
high risk + owner `/approve-risk <full 40-hex sha>` comment equal to the current head -> success
  (only an unedited comment the owner typed: comments posted through a GitHub App, such as
  an agent acting as the owner via the Claude app, do not count)
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
from collections.abc import Callable

from risk_classify import DEFAULT_PATTERNS, DEPENDABOT, added_pins, classify, load_patterns

CONTEXT = "risk-gate"
# The full 40-hex head SHA, matched exactly: a 7-char prefix can be brute-forced in seconds.
APPROVE = re.compile(r"^\s*/approve-risk\s+([0-9a-f]{40})\b", re.IGNORECASE | re.MULTILINE)
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


def find_approval(comments: list[dict], approver: str, sha: str) -> bool:
    """True if `approver` approved `sha` in a comment typed by a person: not posted through
    a GitHub App (an agent acting as the owner via the Claude app sets
    `performed_via_github_app`) and never edited (an app edit of an older owner comment
    would keep that field null)."""
    for c in comments:
        if not approver or c["user"]["login"].lower() != approver:
            continue
        if "performed_via_github_app" not in c or c["performed_via_github_app"] is not None:
            continue
        if c.get("updated_at") != c.get("created_at"):
            continue
        for approved in APPROVE.findall(c.get("body") or ""):
            if approved.lower() == sha.lower():
                return True
    return False


def resolve_tag(action: str, tag: str) -> str | None:
    """The commit SHA that `tag` names in the action's own repo, or None if it can't be read.
    Annotated tags are dereferenced. Unlike GET commits/{sha}, this can't be satisfied by a
    commit pushed to a fork (GitHub serves fork-network commits under the parent's name)."""
    owner_repo = "/".join(action.split("/")[:2])
    try:
        obj = gh_api(f"repos/{owner_repo}/git/ref/tags/{tag}")["object"]  # type: ignore[index]
        for _ in range(5):
            if obj["type"] == "commit":
                return str(obj["sha"])
            if obj["type"] != "tag":
                return None
            obj = gh_api(f"repos/{owner_repo}/git/tags/{obj['sha']}")["object"]  # type: ignore[index]
    except (subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None


def classify_pr(
    pr: dict,
    api_files: list[dict],
    resolve: Callable[[str, str], str | None] = resolve_tag,
) -> dict:
    """Risk of a PR from its API metadata and file list (with patches)."""
    author = pr["user"]["login"]
    files = []
    patches: dict[str, str | None] = {}
    for f in api_files:
        files.append(f["filename"])
        patches[f["filename"]] = f.get("patch")  # absent when too large or binary -> high risk
        if f.get("previous_filename"):
            files.append(f["previous_filename"])  # no patch under the old name -> high risk

    result = classify(files, load_patterns(DEFAULT_PATTERNS), author, patches)
    changed = pr.get("changed_files")
    if not isinstance(changed, int) or changed > len(api_files):
        # The files API stops at 3000; files it didn't list could be high risk.
        result["risk"] = "high"
        result["reason"] = f"file list incomplete ({len(api_files)} of {changed} listed)"
        result["matches"].append({"path": "(unlisted files)", "pattern": "file list incomplete"})
        return result
    if result["risk"] == "standard" and author in DEPENDABOT and result["matches"]:
        for m in result["matches"]:
            for action, sha, tag in added_pins(patches.get(m["path"])):
                if resolve(action, tag) != sha:
                    result["risk"] = "high"
                    result["reason"] = f"{action}@{sha[:12]} is not tag {tag} of that repo"
                    return result
    return result


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
    if pr.get("state") != "open":
        print("PR is not open; nothing to do")
        return 0

    result = classify_pr(pr, gh_api(f"repos/{repo}/pulls/{pr_number}/files", paginate=True))
    if result["risk"] == "standard":
        set_status(repo, sha, "success", f"Standard risk: {result['reason']}")
        return 0

    comments = gh_api(f"repos/{repo}/issues/{pr_number}/comments", paginate=True)
    if find_approval(comments, approver, sha):
        set_status(repo, sha, "success", f"High risk, approved by {approver} for {sha[:7]}")
        return 0

    set_status(repo, sha, "pending", f"High risk: owner must comment /approve-risk {sha}")
    marker = MARKER.format(sha=sha)
    if not any(marker in (c.get("body") or "") for c in comments):
        paths = "\n".join(f"- `{m['path']}` (matches `{m['pattern']}`)" for m in result["matches"][:20])
        more = "" if len(result["matches"]) <= 20 else f"\n- …and {len(result['matches']) - 20} more"
        body = (
            f"{marker}\n### Owner approval needed (risk-gate)\n"
            f"This PR is high risk ({result['reason']}):\n{paths}{more}\n\n"
            "Read the **Owner decision needed** section of the PR description. "
            f"To approve this exact commit, reply with the full SHA:\n\n```\n/approve-risk {sha}\n```\n\n"
            "Type it in the GitHub web UI or app; comments posted through an app (such as the "
            "agent's) and edited comments don't count. Any new push needs a fresh approval. Once approved, auto-merge (if armed) completes "
            "the merge; otherwise tap Squash and merge."
        )
        gh_api(f"repos/{repo}/issues/{pr_number}/comments", "-X", "POST", "-f", f"body={body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
