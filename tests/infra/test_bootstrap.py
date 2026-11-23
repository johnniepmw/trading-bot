"""Bootstrap settings the deploy pipeline's safety depends on (tech debt batch 3; ADR-0019)."""

from __future__ import annotations

import re
from pathlib import Path

BOOTSTRAP = (Path(__file__).resolve().parents[2] / "infra" / "bootstrap" / "main.tf").read_text()


def block(kind: str, name: str) -> str:
    start = BOOTSTRAP.index(f'resource "{kind}" "{name}"')
    end = BOOTSTRAP.find('\nresource "', start + 1)
    return BOOTSTRAP[start : end if end != -1 else None]


def test_the_deployer_holds_no_project_wide_cloud_run_write_role() -> None:
    assert "roles/run.developer" not in block("google_project_iam_member", "deployer")
    assert "roles/run.admin" not in block("google_project_iam_member", "deployer")


def test_the_deployers_project_role_reads_and_cancels_only() -> None:
    permissions = set(
        re.findall(r'"(run\.[a-z.]+)"', block("google_project_iam_custom_role", "job_deployer_reader"))
    )
    assert permissions == {
        "run.jobs.get",
        "run.jobs.list",
        "run.executions.get",
        "run.executions.list",
        "run.executions.cancel",
        "run.operations.get",
        "run.operations.list",
        "run.locations.list",
    }


def test_rollback_images_are_never_cleaned_up() -> None:
    repo = block("google_artifact_registry_repository", "images")
    assert re.search(r'id\s*=\s*"keep-rollback"\s*\n\s*action\s*=\s*"KEEP"', repo)
    assert 'tag_prefixes = ["previous-"]' in repo


def test_the_dashboard_identities_are_created_by_the_owner() -> None:
    text = BOOTSTRAP
    assert '"staging-dashboard", "prod-dashboard"' in text
    # CI may act as them only through the same narrow actAs as the job identities
    assert 'setproduct(["ci-deployer", "ci-infra-apply"], tolist(local.runtime_accounts))' in text


def test_the_iap_api_is_enabled_but_ci_gets_no_iap_role() -> None:
    text = BOOTSTRAP
    assert '"iap.googleapis.com"' in text
    assert "roles/iap" not in text  # IAP access and settings are the owner's script, never CI
