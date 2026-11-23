"""The job's deploy, rollback and flag-override scripts, against a fake `gcloud` (no network).

Phase 2 step 6 (AC13; ADR-0019): image-only updates, a check execution read by status
only, rollback to the previous image on failure, the prod deploy window, and flag writes
that never wipe an override after a read error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"
REPO = "europe-west2-docker.pkg.dev/p/trading-bot"  # <region>-docker.pkg.dev/<project>/<app>
OLD = f"{REPO}/trading-bot@sha256:{'a' * 64}"
NEW = f"{REPO}/trading-bot@sha256:{'b' * 64}"

# Answers from files in $FAKE_DIR; every call is appended to calls.log.
FAKE_GCLOUD = r"""#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]
a = sys.argv[1:]
def path(name): return os.path.join(d, name)
def read(name, default=""):
    return open(path(name)).read() if os.path.exists(path(name)) else default
with open(path("calls.log"), "a") as log:
    log.write(" ".join(a) + "\n")
cmd = " ".join(a[:3])
if cmd == "run jobs executions" and a[3] == "cancel":
    pass
elif cmd == "run jobs executions":
    if os.path.exists(path("list_fails")):
        sys.exit(1)
    if any(x.startswith("--filter") for x in a):  # as gcloud 2026-11: `:*` is refused
        sys.stderr.write("ERROR: Unknown string format: *\n"); sys.exit(1)
    for name in read("running").split():
        print(name + "\t")  # no completion time: still running
    print("trading-bot-x-done\t2026-11-23T07:16:02.123Z")  # a finished one is never "running"
elif cmd == "run services list":
    if os.path.exists(path("services_list_fails")):
        sys.exit(1)
    print(read("services"))
elif cmd == "run services update":
    if os.path.exists(path("service_fails")):
        sys.exit(1)
    open(path("service_image"), "w").write(a[a.index("--image") + 1])
elif cmd == "scheduler jobs describe":
    print(read("scheduler", "PAUSED\t"))
elif cmd == "run jobs describe":
    print(read("image"))
elif cmd == "run jobs update":
    open(path("image"), "w").write(a[a.index("--image") + 1])
elif cmd == "run jobs execute":
    results = read("executions", "ok").split()
    open(path("executions"), "w").write(" ".join(results[1:]) or "ok")
    if results[0] == "hang":
        import time; time.sleep(30)
    sys.exit(0 if results[0] == "ok" else 1)
elif cmd == "artifacts docker tags" and os.path.exists(path("tag_fails")):
    sys.exit(1)
elif cmd == "artifacts docker images":
    print(read("previous_digest"))
elif cmd == "storage objects describe":
    if os.path.exists(path("flags_error")):
        sys.stderr.write(read("flags_error")); sys.exit(1)
    if not os.path.exists(path("flags")):
        sys.stderr.write("ERROR: gs://x/flags.json not found: 404\n"); sys.exit(1)
    print(read("generation", "7"))
elif cmd.startswith("storage cat"):
    print(read("flags"))
elif cmd.startswith("storage rm"):
    if os.path.exists(path("pause_rm_error")):
        sys.stderr.write(read("pause_rm_error")); sys.exit(1)
elif cmd.startswith("storage cp"):
    open(path("written"), "w").write(sys.stdin.read())
"""


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    d = tmp_path / "fake"
    d.mkdir()
    (d / "image").write_text(OLD)
    b = tmp_path / "bin"
    b.mkdir()
    gcloud = b / "gcloud"
    gcloud.write_text(FAKE_GCLOUD)
    gcloud.chmod(0o755)
    return d


def run(fake: Path, script: str, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{fake.parent / 'bin'}:{os.environ['PATH']}",
        "FAKE_DIR": str(fake),
        "GCP_PROJECT": "p",
        "GCP_REGION": "europe-west2",
        "APP_NAME": "trading-bot",
        "AR_REPO": REPO,
        "GITHUB_ACTOR": "owner",
        "CHECK_TIMEOUT": "2",
        **extra,
    }
    return subprocess.run(
        [BASH, str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def calls(fake: Path) -> list[str]:
    log = fake / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def executions(fake: Path) -> list[str]:
    return [c for c in calls(fake) if c.startswith("run jobs execute")]


# --- deploy ---------------------------------------------------------------------------
def test_a_staging_deploy_changes_only_the_image_and_runs_smoke(fake: Path) -> None:
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 0, result.stderr
    assert (fake / "image").read_text() == NEW
    updates = [c for c in calls(fake) if c.startswith("run jobs update")]
    assert updates == [
        f"run jobs update trading-bot-staging --region europe-west2 --image {NEW} --project p --quiet"
    ]
    [execution] = executions(fake)
    assert "--args smoke --wait" in execution


def test_a_deploy_records_the_replaced_image_for_rollback(fake: Path) -> None:
    run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert (
        f"artifacts docker tags add {OLD} {REPO}/trading-bot:previous-staging --project p --quiet"
        in calls(fake)
    )


def test_a_failed_check_rolls_the_job_back(fake: Path) -> None:
    (fake / "executions").write_text("fail")
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 1
    assert (fake / "image").read_text() == OLD
    assert not [c for c in calls(fake) if "tags add" in c]


def test_the_placeholder_image_is_not_recorded_as_a_rollback_target(fake: Path) -> None:
    (fake / "image").write_text("us-docker.pkg.dev/cloudrun/container/job:latest")
    assert run(fake, "cloudrun-job-deploy.sh", "staging", NEW).returncode == 0
    assert not [c for c in calls(fake) if "tags add" in c]


@pytest.mark.parametrize("image", [f"{REPO}/trading-bot:latest", "elsewhere/trading-bot@sha256:" + "c" * 64])
def test_only_a_digest_from_our_registry_is_deployed(fake: Path, image: str) -> None:
    assert run(fake, "cloudrun-job-deploy.sh", "staging", image).returncode == 2
    assert calls(fake) == []


def test_a_prod_deploy_runs_a_dry_run_against_prod(fake: Path) -> None:
    assert run(fake, "cloudrun-job-deploy.sh", "prod", NEW).returncode == 0
    [execution] = executions(fake)
    assert (
        execution.startswith("run jobs execute trading-bot-prod")
        and "--args run,--dry-run,--deploy-check --wait" in execution
    )


def test_a_prod_deploy_waits_while_an_execution_runs(fake: Path) -> None:
    (fake / "running").write_text("trading-bot-prod-abc12")
    result = run(fake, "cloudrun-job-deploy.sh", "prod", NEW)
    assert result.returncode == 75
    assert (fake / "image").read_text() == OLD
    assert "is running" in result.stderr


def test_a_prod_deploy_waits_when_the_next_run_is_close(fake: Path) -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (fake / "scheduler").write_text(f"ENABLED\t{soon}")
    result = run(fake, "cloudrun-job-deploy.sh", "prod", NEW)
    assert result.returncode == 75
    assert (fake / "image").read_text() == OLD


def test_a_prod_deploy_goes_ahead_when_the_next_run_is_far(fake: Path) -> None:
    later = (datetime.now(UTC) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (fake / "scheduler").write_text(f"ENABLED\t{later}")
    assert run(fake, "cloudrun-job-deploy.sh", "prod", NEW).returncode == 0
    assert (fake / "image").read_text() == NEW


def test_staging_has_no_deploy_window(fake: Path) -> None:
    (fake / "running").write_text("trading-bot-staging-x")
    assert run(fake, "cloudrun-job-deploy.sh", "staging", NEW).returncode == 0


# --- rollback ---------------------------------------------------------------------------
def test_rollback_returns_to_the_recorded_previous_image(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    (fake / "previous_digest").write_text("sha256:" + "a" * 64)
    result = run(fake, "cloudrun-job-rollback.sh", "prod")
    assert result.returncode == 0, result.stderr
    assert (fake / "image").read_text() == OLD
    [execution] = executions(fake)
    assert "--args smoke --wait" in execution


def test_rollback_without_a_recorded_image_changes_nothing(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    assert run(fake, "cloudrun-job-rollback.sh", "prod").returncode == 1
    assert (fake / "image").read_text() == NEW


def test_rollback_to_a_named_digest(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    assert (
        run(fake, "cloudrun-job-rollback.sh", "staging", OLD, NAMED_ROLLBACK_ALLOWED="true").returncode == 0
    )
    assert (fake / "image").read_text() == OLD


# --- flag overrides -----------------------------------------------------------------------
def test_kill_keeps_other_overrides_and_writes_with_a_generation_match(fake: Path) -> None:
    (fake / "flags").write_text('{"overrides": {"other": true}}')
    result = run(fake, "flag-override.sh", "prod", "kill", "new-orders")
    assert result.returncode == 0, result.stderr
    assert json.loads((fake / "written").read_text())["overrides"] == {"other": True, "new-orders": False}
    assert next(c for c in calls(fake) if c.startswith("storage cp")).endswith("--if-generation-match=7")


def test_a_read_error_never_wipes_existing_overrides(fake: Path) -> None:
    (fake / "flags").write_text('{"overrides": {"new-orders": false}}')
    (fake / "flags_error").write_text(
        "ERROR: (gcloud.storage.objects.describe) HTTPError 503: backend error\n"
    )
    result = run(fake, "flag-override.sh", "prod", "restore", "new-orders")
    assert result.returncode == 1
    assert not (fake / "written").exists()
    assert "nothing changed" in result.stderr


def test_a_missing_object_starts_from_no_overrides_and_must_still_be_missing(fake: Path) -> None:
    result = run(fake, "flag-override.sh", "staging", "kill", "new-orders")
    assert result.returncode == 0, result.stderr
    assert json.loads((fake / "written").read_text())["overrides"] == {"new-orders": False}
    assert next(c for c in calls(fake) if c.startswith("storage cp")).endswith("--if-generation-match=0")


def test_a_malformed_object_is_left_alone(fake: Path) -> None:
    (fake / "flags").write_text('{"overrides": "on"}')
    assert run(fake, "flag-override.sh", "prod", "kill", "new-orders").returncode == 1
    assert not (fake / "written").exists()


# --- from step 6's security review ----------------------------------------------------------
def test_a_hung_check_execution_is_cancelled_and_rolled_back(fake: Path) -> None:
    (fake / "executions").write_text("hang")
    (fake / "running").write_text("trading-bot-staging-hung1")  # what the hung check leaves behind
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode != 0
    assert (fake / "image").read_text() == OLD
    assert (
        "run jobs executions cancel trading-bot-staging-hung1 --region europe-west2 --project p --quiet"
        in calls(fake)
    )


def test_the_registry_comes_from_the_project_not_a_variable(fake: Path) -> None:
    foreign = "docker.io/x/trading-bot@sha256:" + "d" * 64
    assert run(fake, "cloudrun-job-deploy.sh", "staging", foreign, AR_REPO="docker.io/x").returncode == 2
    assert (fake / "image").read_text() == OLD


def test_a_short_digest_is_refused(fake: Path) -> None:
    assert run(fake, "cloudrun-job-deploy.sh", "staging", f"{REPO}/trading-bot@sha256:abc").returncode == 2


@pytest.mark.parametrize("state", ["UPDATE_FAILED", "", "ENABLING"])
def test_a_scheduler_that_isnt_plainly_paused_needs_a_known_next_run(fake: Path, state: str) -> None:
    (fake / "scheduler").write_text(f"{state}\t")
    assert run(fake, "cloudrun-job-deploy.sh", "prod", NEW).returncode != 0
    assert (fake / "image").read_text() == OLD


def test_a_failed_read_of_running_executions_refuses_the_deploy(fake: Path) -> None:
    (fake / "list_fails").write_text("")
    assert run(fake, "cloudrun-job-deploy.sh", "prod", NEW).returncode != 0
    assert (fake / "image").read_text() == OLD


def test_redeploying_the_same_image_keeps_the_rollback_target(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    assert run(fake, "cloudrun-job-deploy.sh", "staging", NEW).returncode == 0
    assert not [c for c in calls(fake) if "tags add" in c]


def test_a_failed_tag_after_a_good_check_is_only_a_warning(fake: Path) -> None:
    (fake / "tag_fails").write_text("")
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 0
    assert (fake / "image").read_text() == NEW
    assert "warning" in result.stderr


def test_a_rollback_whose_smoke_fails_goes_back(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    (fake / "previous_digest").write_text("sha256:" + "a" * 64)
    (fake / "executions").write_text("fail")
    assert run(fake, "cloudrun-job-rollback.sh", "prod").returncode == 1
    assert (fake / "image").read_text() == NEW


def test_a_named_rollback_digest_needs_the_owner(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    result = run(fake, "cloudrun-job-rollback.sh", "prod", OLD, NAMED_ROLLBACK_ALLOWED="false")
    assert result.returncode == 2
    assert (fake / "image").read_text() == NEW


# --- clearing pause requests (owner only; step 7b) ------------------------------------------
def test_the_owner_clears_every_pause_request(fake: Path) -> None:
    result = run(fake, "pause-clear.sh", "prod", OWNER="true")
    assert result.returncode == 0, result.stderr
    assert "storage rm gs://p-pause-prod/** --project p --quiet" in calls(fake)


def test_nobody_else_can_clear_a_pause(fake: Path) -> None:
    result = run(fake, "pause-clear.sh", "prod", OWNER="false")
    assert result.returncode == 2
    assert calls(fake) == []


def test_clearing_when_nothing_is_paused_is_fine(fake: Path) -> None:
    (fake / "pause_rm_error").write_text(
        "ERROR: (gcloud.storage.rm) The following URLs matched no objects or files:\n"
    )
    assert run(fake, "pause-clear.sh", "staging", OWNER="true").returncode == 0


def test_a_failed_clear_is_reported(fake: Path) -> None:
    (fake / "pause_rm_error").write_text("ERROR: (gcloud.storage.rm) HTTPError 403: denied\n")
    result = run(fake, "pause-clear.sh", "staging", OWNER="true")
    assert result.returncode == 1
    assert "still paused" in result.stderr


# --- the dashboard service follows the job (ADR-0020) ----------------------------------
def test_without_a_dashboard_service_a_deploy_only_moves_the_job(fake: Path) -> None:
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 0
    assert "no trading-bot-dashboard-staging yet; skipped" in result.stdout
    assert not (fake / "service_image").exists()


def test_the_dashboard_follows_the_job_after_its_check_passes(fake: Path) -> None:
    (fake / "services").write_text("other-service\ntrading-bot-dashboard-staging\n")
    assert run(fake, "cloudrun-job-deploy.sh", "staging", NEW).returncode == 0
    assert (fake / "service_image").read_text() == NEW
    assert (fake / "image").read_text() == NEW


def test_a_failed_job_check_never_moves_the_dashboard(fake: Path) -> None:
    (fake / "services").write_text("trading-bot-dashboard-staging\n")
    (fake / "executions").write_text("fail")
    assert run(fake, "cloudrun-job-deploy.sh", "staging", NEW).returncode == 1
    assert not (fake / "service_image").exists()
    assert (fake / "image").read_text() == OLD


@pytest.mark.parametrize("failure", ["service_fails"])
def test_a_dashboard_failure_fails_the_deploy_but_leaves_the_job_on_its_new_image(
    fake: Path, failure: str
) -> None:
    (fake / "services").write_text("trading-bot-dashboard-staging\n")
    (fake / failure).write_text("")
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 1
    assert "DASHBOARD NOT UPDATED" in result.stderr
    assert (fake / "image").read_text() == NEW  # the checked job is never reverted for it


def test_a_rollback_moves_the_dashboard_too(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    (fake / "previous_digest").write_text("sha256:" + "a" * 64)
    (fake / "services").write_text("trading-bot-dashboard-prod\n")
    assert run(fake, "cloudrun-job-rollback.sh", "prod").returncode == 0
    assert (fake / "service_image").read_text() == OLD


def test_a_failed_dashboard_rollback_leaves_the_job_rolled_back(fake: Path) -> None:
    (fake / "image").write_text(NEW)
    (fake / "previous_digest").write_text("sha256:" + "a" * 64)
    (fake / "services").write_text("trading-bot-dashboard-prod\n")
    (fake / "service_fails").write_text("")
    result = run(fake, "cloudrun-job-rollback.sh", "prod")
    assert result.returncode == 1
    assert "DASHBOARD NOT ROLLED BACK" in result.stderr
    assert (fake / "image").read_text() == OLD


def test_a_deployer_that_cant_list_services_skips_the_dashboard_with_a_warning(fake: Path) -> None:
    # before the owner's bootstrap grants run.services.list
    (fake / "services_list_fails").write_text("")
    result = run(fake, "cloudrun-job-deploy.sh", "staging", NEW)
    assert result.returncode == 0
    assert "couldn't list services" in result.stderr
    assert (fake / "image").read_text() == NEW
