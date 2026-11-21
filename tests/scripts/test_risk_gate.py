"""Tests for scripts/risk_gate.py: which comments count as the owner's approval."""

from __future__ import annotations

from typing import Any

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
