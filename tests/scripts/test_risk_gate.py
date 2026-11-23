"""Tests for scripts/risk_gate.py: which comments count as the owner's approval."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
import risk_gate
from risk_gate import classify_pr, find_approval

SHA = "abcdef0123456789abcdef0123456789abcdef01"
OWNER = "owner"
APPROVAL = "/approve-risk " + SHA
TS = "2026-11-21T15:03:45Z"


def comment(
    body: str = APPROVAL,
    login: str = OWNER,
    app: dict[str, Any] | None = None,
    created: str = TS,
    updated: str = TS,
) -> dict[str, Any]:
    return {
        "body": body,
        "user": {"login": login},
        "performed_via_github_app": app,
        "created_at": created,
        "updated_at": updated,
    }


def test_owner_web_comment_approves() -> None:
    assert find_approval([comment()], OWNER, SHA)


def test_login_match_is_case_insensitive() -> None:
    assert find_approval([comment(login="Owner")], OWNER, SHA)


def test_comment_posted_through_an_app_is_ignored() -> None:
    assert not find_approval([comment(app={"slug": "claude", "id": 1236702})], OWNER, SHA)


def test_app_comment_does_not_hide_a_real_approval() -> None:
    comments = [comment(app={"slug": "claude"}), comment()]
    assert find_approval(comments, OWNER, SHA)


def test_edited_comment_is_ignored() -> None:
    assert not find_approval([comment(updated="2026-11-21T16:00:00Z")], OWNER, SHA)


def test_comment_without_the_app_field_is_ignored() -> None:
    c = comment()
    del c["performed_via_github_app"]
    assert not find_approval([c], OWNER, SHA)


def test_other_login_is_ignored() -> None:
    assert not find_approval([comment(login="someone")], OWNER, SHA)


def test_wrong_sha_is_ignored() -> None:
    assert not find_approval([comment(body="/approve-risk " + "1" * 40)], OWNER, SHA)


def test_short_sha_is_ignored() -> None:
    assert not find_approval([comment(body="/approve-risk " + SHA[:7])], OWNER, SHA)


def test_approval_of_a_sha_sharing_the_prefix_does_not_carry_over() -> None:
    # An agent can grind a new commit whose SHA starts with an approved one's 7 characters.
    other = SHA[:7] + "0" * 33
    assert not find_approval([comment(body="/approve-risk " + other)], OWNER, SHA)
    assert not find_approval([comment()], OWNER, other)


def test_empty_approver_matches_nobody() -> None:
    assert not find_approval([comment()], "", SHA)


# classify_pr: the PR-level checks around classify()

OLD = "3d3c42e5aac5ba805825da76410c181273ba90b1"
NEW = "0123456789abcdef0123456789abcdef01234567"
WF = ".github/workflows/ci.yml"
BUMP = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: actions/checkout@{NEW} # v7.0.2"


def pr(author: str = "dependabot[bot]", changed: int | None = 1) -> dict[str, Any]:
    return {"user": {"login": author}, "changed_files": changed}


def tags(table: dict[tuple[str, str], str]) -> Any:
    calls: list[tuple[str, str]] = []

    def resolve(action: str, tag: str) -> str | None:
        calls.append((action, tag))
        return table.get((action, tag))

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def test_dependabot_bump_to_the_tagged_sha_is_standard() -> None:
    resolve = tags({("actions/checkout", "v7.0.2"): NEW})
    result = classify_pr(pr(), [{"filename": WF, "patch": BUMP}], resolve)
    assert result["risk"] == "standard"
    assert resolve.calls == [("actions/checkout", "v7.0.2")]


def test_dependabot_bump_to_a_sha_the_tag_does_not_name_is_high() -> None:
    # e.g. an "imposter" commit pushed to a fork of actions/checkout
    resolve = tags({("actions/checkout", "v7.0.2"): OLD})
    assert classify_pr(pr(), [{"filename": WF, "patch": BUMP}], resolve)["risk"] == "high"


def test_dependabot_bump_with_an_unresolvable_tag_is_high() -> None:
    assert classify_pr(pr(), [{"filename": WF, "patch": BUMP}], tags({}))["risk"] == "high"


def test_non_dependabot_standard_pr_does_not_resolve_tags() -> None:
    resolve = tags({})
    result = classify_pr(pr("someone"), [{"filename": "docs/x.md", "patch": "+x"}], resolve)
    assert result["risk"] == "standard"
    assert resolve.calls == []


def test_truncated_file_list_is_high() -> None:
    files = [{"filename": "docs/x.md", "patch": "+x"}]
    assert classify_pr(pr("someone", changed=3001), files, tags({}))["risk"] == "high"


def test_missing_changed_files_count_is_high() -> None:
    files = [{"filename": "docs/x.md", "patch": "+x"}]
    assert classify_pr(pr("someone", changed=None), files, tags({}))["risk"] == "high"


def test_renamed_file_counts_under_both_names() -> None:
    files = [{"filename": "docs/x.md", "previous_filename": "scripts/x.py", "patch": ""}]
    assert classify_pr(pr("someone"), files, tags({}))["risk"] == "high"


# The risk-gate status is posted with the dedicated app's token, never the workflow token


def test_set_status_uses_the_status_token(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"cmd": cmd, "env": kw.get("env")})
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setenv("GH_TOKEN", "workflow-token")
    monkeypatch.setenv("STATUS_TOKEN", "app-token")
    monkeypatch.setattr(risk_gate.subprocess, "run", fake_run)
    risk_gate.set_status("o/r", SHA, "success", "ok")
    assert len(seen) == 1
    assert seen[0]["cmd"][2] == f"repos/o/r/statuses/{SHA}"
    assert seen[0]["env"]["GH_TOKEN"] == "app-token"  # noqa: S105 (fake value)


def test_main_fails_closed_without_a_status_token(monkeypatch: Any) -> None:
    def no_api(*a: Any, **k: Any) -> Any:
        raise AssertionError("no API call without a status token")

    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR", "1")
    monkeypatch.delenv("STATUS_TOKEN", raising=False)
    monkeypatch.setattr(risk_gate, "gh_api", no_api)
    with pytest.raises(SystemExit) as exc:
        risk_gate.main()
    assert exc.value.code != 0


# --- tech debt batch 2 -----------------------------------------------------------------------
DOWNGRADE = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: actions/checkout@{NEW} # v6.9.9"


def test_dependabot_downgrade_is_high_even_when_the_tag_resolves() -> None:
    # an older release can carry a known vulnerability: only upgrades are routine
    resolve = tags({("actions/checkout", "v6.9.9"): NEW})
    result = classify_pr(pr(), [{"filename": WF, "patch": DOWNGRADE}], resolve)
    assert result["risk"] == "high"
    assert "not newer" in result["reason"]


def test_dependabot_bump_to_the_same_version_is_high() -> None:
    same = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: actions/checkout@{NEW} # v7.0.1"
    resolve = tags({("actions/checkout", "v7.0.1"): NEW})
    assert classify_pr(pr(), [{"filename": WF, "patch": same}], resolve)["risk"] == "high"


def test_version_order_is_numeric_not_textual() -> None:
    ten = f"-      - uses: actions/checkout@{OLD} # v7.9\n+      - uses: actions/checkout@{NEW} # v7.10"
    resolve = tags({("actions/checkout", "v7.10"): NEW})
    assert classify_pr(pr(), [{"filename": WF, "patch": ten}], resolve)["risk"] == "standard"


class FakeApi:
    """gh_api stand-in: the PR (twice, so the head can move), its files and comments; records statuses."""

    def __init__(self, heads: list[str], comments: list[dict[str, Any]]) -> None:
        self.heads, self.comments, self.statuses = list(heads), comments, []

    def __call__(self, path: str, *args: str, paginate: bool = False, token: str | None = None) -> Any:
        if path.endswith("/files"):
            return [{"filename": "src/tradingbot/risk/limits.py", "patch": "+x"}]
        if path.endswith("/comments") and "-X" not in args:
            return self.comments
        if "/statuses/" in path:
            self.statuses.append((path.rsplit("/", 1)[1], args))
            return {}
        if path.endswith("/comments"):
            return {}
        head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        return {"head": {"sha": head}, "state": "open", "user": {"login": "someone"}, "changed_files": 1}


def run_main(monkeypatch: Any, api: FakeApi) -> None:
    monkeypatch.setenv("STATUS_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR", "1")
    monkeypatch.setenv("APPROVER", OWNER)
    monkeypatch.setattr(risk_gate, "gh_api", api)
    assert risk_gate.main() == 0


def test_an_approval_counts_when_the_head_stayed_put(monkeypatch: Any) -> None:
    api = FakeApi([SHA, SHA], [comment()])
    run_main(monkeypatch, api)
    assert [s for s, _ in api.statuses] == [SHA]
    assert "state=success" in api.statuses[0][1]


def test_no_status_is_set_when_the_head_moved_during_the_check(monkeypatch: Any) -> None:
    # approved SHA A; a new commit lands while files and comments are read: A must not pass
    moved = "1111111111111111111111111111111111111111"
    api = FakeApi([SHA, moved], [comment()])
    run_main(monkeypatch, api)
    assert api.statuses == []  # the push's own run decides for the new head
