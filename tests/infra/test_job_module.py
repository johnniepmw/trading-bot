"""Settings in the job's OpenTofu that the app's safety depends on (Phase 2 AC12; ADR-0017).

`tofu validate` and `plan` need provider access (CI and the owner's `make env-up`); these
tests pin the values the Python side relies on, so a change to either side is caught.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tradingbot.app.remote_ledger import STALE_AFTER
from tradingbot.config.load import load_config

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "modules" / "cloud-run-job"
STAGING, PROD = (ROOT / "infra" / "envs" / env / "main.tf" for env in ("staging", "prod"))


def variable_default(name: str) -> str:
    text = (MODULE / "variables.tf").read_text()
    block = text[text.index(f'variable "{name}"') :]
    match = re.search(r"default\s*=\s*(.+)", block)
    assert match is not None
    return match.group(1).strip().strip('"')


def resource(kind: str, name: str) -> str:
    text = (MODULE / "main.tf").read_text()
    start = text.index(f'resource "{kind}" "{name}"')
    end = text.find('\nresource "', start + 1)
    return text[start : end if end != -1 else None]


def test_the_job_times_out_before_its_lock_goes_stale() -> None:
    # a hung run past its pre-send checkpoint must be dead before anyone may clear its lock
    assert int(variable_default("timeout_seconds")) < STALE_AFTER.total_seconds()


@pytest.mark.parametrize("env", [STAGING, PROD])
def test_no_environment_overrides_the_timeout_or_the_schedule(env: Path) -> None:
    text = env.read_text()
    assert "timeout_seconds" not in text
    assert not re.search(r"^\s*schedule\s*=", text, re.MULTILINE)


def test_the_schedule_matches_the_config_files_time() -> None:
    minute, hour, *_ = variable_default("schedule").split()
    scheduled = load_config().schedule_time
    assert (int(hour), int(minute)) == (scheduled.hour, scheduled.minute)


def test_the_schedule_is_created_paused_and_applies_never_resume_it() -> None:
    block = resource("google_cloud_scheduler_job", "daily")
    assert re.search(r"paused\s*=\s*true", block)
    assert "ignore_changes = [paused]" in block


def test_the_job_never_retries_an_attempt() -> None:
    assert re.search(r"max_retries\s*=\s*0", resource("google_cloud_run_v2_job", "this"))


@pytest.mark.parametrize("name", ["ledger", "locks"])
def test_ledger_and_lock_buckets_are_never_destroyed_and_keep_deletions(name: str) -> None:
    block = resource("google_storage_bucket", name)
    assert re.search(r"prevent_destroy\s*=\s*true", block)
    assert re.search(r"force_destroy\s*=\s*false", block)
    assert "soft_delete_policy" in block


def test_the_ledger_bucket_keeps_every_version() -> None:
    assert re.search(r"versioning \{\s*enabled = true", resource("google_storage_bucket", "ledger"))


@pytest.mark.parametrize("local", ["ledger_bucket", "lock_bucket"])
def test_bucket_names_carry_the_environment(local: str) -> None:
    # the job refuses staging or prod buckets not named -<env> (app/job.py)
    text = (MODULE / "main.tf").read_text()
    assert re.search(rf'{local}\s*=\s*"\$\{{var.project_id\}}-\w+-\$\{{var.env\}}"', text)


def test_the_flags_seed_is_never_overwritten() -> None:
    assert "ignore_changes = all" in resource("google_storage_bucket_object", "flags_seed")


def test_the_live_switch_is_mounted_where_the_config_reads_it() -> None:
    assert re.search(r'owner_secrets\s*=\s*\{ "prod-operator" = local.b.operator_secret \}', PROD.read_text())
    assert load_config().operator_secret_path == "/secrets/prod-operator/value"  # noqa: S105 - a path


def test_staging_has_no_live_switch() -> None:
    assert "owner_secrets" not in STAGING.read_text()


def test_no_service_account_is_created_outside_the_bootstrap() -> None:
    sources = "\n".join(p.read_text() for p in (ROOT / "infra").rglob("*.tf") if "bootstrap" not in p.parts)
    assert 'resource "google_service_account"' not in sources


def test_opentofu_never_writes_a_secret_value() -> None:
    sources = "\n".join(p.read_text() for p in (ROOT / "infra").rglob("*.tf") if "bootstrap" not in p.parts)
    assert "google_secret_manager_secret_version" not in sources  # state holds no value; CI reads none


def test_the_job_has_no_object_or_folder_iam_powers() -> None:
    text = (MODULE / "main.tf").read_text()
    assert 'role   = "roles/storage.objectAdmin"\n  member = "serviceAccount:${var.runtime_sa}"' not in text


def test_the_image_takes_its_command_from_the_jobs_args() -> None:
    # Cloud Run's args replace CMD: with no ENTRYPOINT, `run` would be the program itself
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["python", "-m", "tradingbot.app"]' in dockerfile
    assert 'CMD ["smoke"]' in dockerfile


def test_alerts_match_only_the_bots_own_alert_lines_or_a_failed_schedule() -> None:
    block = resource("google_monitoring_alert_policy", "bot_alert")
    assert 'resource.labels.job_name=\\"${local.job_name}\\" AND jsonPayload.alert:*' in block
    assert 'resource.labels.job_id=\\"${local.job_name}-daily\\" AND severity>=ERROR' in block
    assert "textPayload" not in block  # Cloud Run's own "task failed" entries never page
    assert 'kind = "EXTRACT(jsonPayload.alert)"' in block
    assert "$${log.extracted_label.kind}" in block  # the email names the kind


def test_one_alert_condition_per_environment() -> None:
    assert (MODULE / "main.tf").read_text().count("condition_matched_log") == 1


@pytest.mark.parametrize("env", [STAGING, PROD])
def test_alerts_go_to_the_owners_email(env: Path) -> None:
    assert re.search(r"alert_email\s*=\s*local\.b\.alert_email", env.read_text())
