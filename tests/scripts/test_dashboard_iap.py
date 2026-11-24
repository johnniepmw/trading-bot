"""The owner's IAP script, against a fake gcloud: only the owner's account gets in, IAP's agent
is the only invoker, public access is removed, and the OAuth secret is never printed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"
FAKE = r"""#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
if a[:2] == ["config", "get-value"]:
    print({"project": "p", "account": "owner@example.com"}[a[2]])
elif a[:3] == ["run", "services", "describe"]:
    if os.path.exists(os.path.join(d, "no_service")): sys.exit(1)
    print("https://dash.example.run.app" if "status.url" in " ".join(a) else a[3])
elif a[:2] == ["iap", "settings"]:
    open(os.path.join(d, "settings"), "w").write(open(a[3]).read())
elif a[:2] == ["projects", "describe"]:
    print("123")
elif a[:3] == ["run", "services", "get-iam-policy"]:
    print(open(os.path.join(d, "policy")).read() if os.path.exists(os.path.join(d, "policy")) else "{}")
"""


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    d = tmp_path / "fake"
    d.mkdir()
    b = tmp_path / "bin"
    b.mkdir()
    (b / "gcloud").write_text(FAKE)
    (b / "gcloud").chmod(0o755)
    return d


def run(fake: Path, *args: str, client: bool = True) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{fake.parent / 'bin'}:{os.environ['PATH']}", "FAKE_DIR": str(fake)}
    if client:
        env |= {"IAP_CLIENT_ID": "client-123.apps.example", "IAP_CLIENT_SECRET": "s3cr3t-value"}
    return subprocess.run(
        [BASH, str(ROOT / "scripts" / "dashboard-iap.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def calls(fake: Path) -> list[str]:
    return (fake / "calls.log").read_text().splitlines()


def test_it_puts_the_service_behind_iap_for_the_owner_only(fake: Path) -> None:
    result = run(fake, "staging")
    assert result.returncode == 0, result.stderr
    log = "\n".join(calls(fake))
    assert "run services update trading-bot-dashboard-staging --region europe-west2 --iap" in log
    assert (
        "--member=serviceAccount:service-123@gcp-sa-iap.iam.gserviceaccount.com --role=roles/run.invoker"
        in log
    )
    assert "--member=user:owner@example.com --role=roles/iap.httpsResourceAccessor" in log
    assert "allUsers" not in log  # nothing to remove
    assert "https://dash.example.run.app" in result.stdout


def test_the_oauth_client_goes_to_iap_and_the_secret_is_never_printed(fake: Path) -> None:
    result = run(fake, "prod")
    assert 'client_secret: "s3cr3t-value"' in (fake / "settings").read_text()
    assert "s3cr3t-value" not in result.stdout + result.stderr + "\n".join(calls(fake))


@pytest.mark.parametrize("public", ["allUsers", "allAuthenticatedUsers"])
def test_public_access_is_removed(fake: Path, public: str) -> None:
    (fake / "policy").write_text(
        f'{{"bindings": [{{"members": ["{public}"], "role": "roles/run.invoker"}}]}}'
    )
    assert run(fake, "staging").returncode == 0
    assert any("remove-iam-policy-binding" in c and f"--member={public}" in c for c in calls(fake))


def test_it_needs_the_oauth_client(fake: Path) -> None:
    result = run(fake, "staging", client=False)
    assert result.returncode == 2
    assert not (fake / "calls.log").exists()


def test_it_needs_the_service_to_exist(fake: Path) -> None:
    (fake / "no_service").write_text("")
    result = run(fake, "staging")
    assert result.returncode == 1
    assert not any("--iap" in c for c in calls(fake))


def test_unknown_environments_are_refused(fake: Path) -> None:
    assert run(fake, "dev").returncode == 2
