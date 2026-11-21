"""Tests for scripts/risk_gate.py: which comments count as the owner's approval."""

from __future__ import annotations

from typing import Any

from risk_gate import find_approval

SHA = "abcdef0123456789abcdef0123456789abcdef01"
OWNER = "owner"
APPROVAL = "/approve-risk " + SHA[:7]
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
    assert not find_approval([comment(body="/approve-risk 1234567")], OWNER, SHA)


def test_empty_approver_matches_nobody() -> None:
    assert not find_approval([comment()], "", SHA)
