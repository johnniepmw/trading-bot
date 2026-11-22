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
REPO = "europe-west2-docker.pkg.dev/p/trading-bot"
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
if cmd == "run jobs executions":
    print(read("running"))
elif cmd == "scheduler jobs describe":
    print(read("scheduler", "PAUSED\t"))
elif cmd == "run jobs describe":
    print(read("image"))
elif cmd == "run jobs update":
    open(path("image"), "w").write(a[a.index("--image") + 1])
elif cmd == "run jobs execute":
    results = read("executions", "ok").split()
    open(path("executions"), "w").write(" ".join(results[1:]) or "ok")
    sys.exit(0 if results[0] == "ok" else 1)
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


def run(fake: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{fake.parent / 'bin'}:{os.environ['PATH']}",
        "FAKE_DIR": str(fake),
        "GCP_PROJECT": "p",
        "GCP_REGION": "europe-west2",
        "APP_NAME": "trading-bot",
        "AR_REPO": REPO,
        "GITHUB_ACTOR": "owner",
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
        and "--args run,--dry-run --wait" in execution
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
    assert run(fake, "cloudrun-job-rollback.sh", "staging", OLD).returncode == 0
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
