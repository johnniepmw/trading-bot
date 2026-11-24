"""The dashboard service's OpenTofu (ADR-0020): never public, IAP left to the owner, the identity's
storage access exactly as decided, the image left to the deploy pipeline."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = (ROOT / "infra" / "modules" / "cloud-run-dashboard" / "main.tf").read_text()
JOB = (ROOT / "infra" / "modules" / "cloud-run-job" / "main.tf").read_text()
ENVS = {env: (ROOT / "infra" / "envs" / env / "main.tf").read_text() for env in ("staging", "prod")}
ALL_TF = {p: p.read_text() for p in sorted((ROOT / "infra").glob("**/*.tf")) if "bootstrap" not in p.parts}


def test_the_dashboard_is_never_public() -> None:
    assert "allUsers" not in MODULE.replace("never grants allUsers", "")
    assert "allAuthenticatedUsers" not in MODULE.replace("or allAuthenticatedUsers", "")
    assert re.search(r"invoker_iam_disabled\s*=\s*false", MODULE)
    assert "roles/run.invoker" not in MODULE  # the owner's script grants it to IAP's agent only
    assert "_iam_policy" not in MODULE and "_iam_binding" not in MODULE  # never authoritative


def _code(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())  # comments don't count


def test_no_infra_file_can_make_any_cloud_run_service_public() -> None:
    # the module test alone isn't enough: an env file or another module could loosen it
    for path, text in ALL_TF.items():
        code = _code(text)
        assert "allUsers" not in code and "allAuthenticatedUsers" not in code, path
        assert all(v == "false" for v in re.findall(r"invoker_iam_disabled\s*=\s*(\S+)", code)), path
        assert not re.search(r"google_cloud_run_v2_service_iam_(policy|binding)", code), path
        assert not re.search(r"iap_enabled\s*=\s*false", code), path
        if "google_cloud_run_v2_service_iam_member" in code:
            assert "roles/run.invoker" not in code, path


def test_iap_and_the_image_are_left_to_the_owner_and_the_pipeline() -> None:
    ignored = MODULE[MODULE.index("ignore_changes") : MODULE.index("]\n", MODULE.index("ignore_changes = ["))]
    for attribute in ("template[0].containers[0].image", "iap_enabled", "client"):
        assert attribute in ignored
    assert "google_iap" not in MODULE  # IAP settings, OAuth client and access: the owner's script


def test_the_identity_reads_the_ledger_and_lock_and_creates_pause_requests() -> None:
    grants = re.findall(r'bucket = var\.(\w+)\n  role   = "([^"]+)"', MODULE)
    assert sorted(grants) == [
        ("ledger_bucket", "roles/storage.objectViewer"),
        ("lock_bucket", "roles/storage.objectViewer"),
        ("pause_bucket", "roles/storage.objectCreator"),
        ("pause_bucket", "roles/storage.objectViewer"),
    ]
    assert "objectAdmin" not in MODULE and "objectUser" not in MODULE  # never delete or overwrite


def test_the_deployer_gets_the_no_invoke_role() -> None:
    assert re.search(r"role\s*=\s*var\.deployer_role", MODULE)
    assert "roles/run.developer" not in MODULE


def test_the_service_runs_serve_on_its_own_identity() -> None:
    assert 'args  = ["serve"]' in MODULE
    assert re.search(r"service_account\s*=\s*var\.runtime_sa", MODULE)
    assert re.search(r"max_instance_count\s*=\s*1", MODULE)


def test_prod_is_protected_from_deletion() -> None:
    assert 'deletion_protection = var.env == "prod"' in MODULE


def test_each_environment_wires_its_own_dashboard_identity() -> None:
    for env, text in ENVS.items():
        assert f'runtime_sa    = local.b.sa_runtime["{env}-dashboard"]' in text
        assert "deployer_role = local.b.dashboard_deployer_role" in text
        assert "pause_bucket  = module.job.pause_bucket" in text


def test_the_dashboards_alert_lines_reach_the_owner() -> None:
    service = 'resource.labels.service_name=\\"${var.app_name}-dashboard-${var.env}\\"'
    assert f'resource.type=\\"cloud_run_revision\\" AND {service} AND jsonPayload.alert:*' in JOB
