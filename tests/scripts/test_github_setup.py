"""Tests for scripts/github-setup.sh's risk-gate parts, with a fake `gh` (no network)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github-setup.sh"
BASH = shutil.which("bash") or "/bin/bash"

# Fake gh: answers the calls github-setup.sh makes from files in $FAKE_DIR, and records
# the ruleset it is sent. Paths with `-q` get the already-filtered value, like real gh.
FAKE_GH = r"""#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]
a = sys.argv[1:]
def read(name, default=""):
    p = os.path.join(d, name)
    return open(p).read() if os.path.exists(p) else default
if a[:1] != ["api"]:
    sys.exit(0)  # label create etc.
path = next(x for x in a[1:] if not x.startswith("-") and x not in ("PUT", "POST", "PATCH"))
method = a[a.index("-X") + 1] if "-X" in a else "GET"
if path.endswith("/deployment-branch-policies"):
    if method == "POST":
        open(os.path.join(d, "policies"), "w").write("branch:main")
    else:
        print(read("policies"))
elif path.endswith("/rulesets") and method == "GET":
    print(read("ruleset_id"))
elif "/rulesets/" in path and method == "GET":
    print(read("pin"))
elif "/rulesets" in path:
    open(os.path.join(d, "ruleset.json"), "w").write(sys.stdin.read())
"""


def run(tmp_path: Path, files: dict[str, str], **env: str) -> subprocess.CompletedProcess[str]:
    d = tmp_path / "fake"
    d.mkdir()
    for name, value in files.items():
        (d / name).write_text(value)
    b = tmp_path / "bin"
    b.mkdir()
    gh = b / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    full_env = {"PATH": f"{b}:{os.environ['PATH']}", "FAKE_DIR": str(d), "REPO": "o/r", **env}
    return subprocess.run([BASH, str(SCRIPT)], env=full_env, capture_output=True, text=True, check=False)


def risk_gate_check(tmp_path: Path) -> dict[str, object]:
    ruleset = json.loads((tmp_path / "fake" / "ruleset.json").read_text())
    rule = next(r for r in ruleset["rules"] if r["type"] == "required_status_checks")
    return next(c for c in rule["parameters"]["required_status_checks"] if c["context"] == "risk-gate")


def test_unpinned_by_default(tmp_path: Path) -> None:
    proc = run(tmp_path, {"policies": "branch:main", "ruleset_id": "7"})
    assert proc.returncode == 0, proc.stderr
    assert risk_gate_check(tmp_path) == {"context": "risk-gate"}
    assert "name only" in proc.stdout


def test_pins_to_the_app_id(tmp_path: Path) -> None:
    proc = run(tmp_path, {"policies": "branch:main", "ruleset_id": "7"}, RISK_GATE_APP_ID="123456")
    assert proc.returncode == 0, proc.stderr
    assert risk_gate_check(tmp_path) == {"context": "risk-gate", "integration_id": 123456}


def test_rerun_without_the_id_keeps_the_pin(tmp_path: Path) -> None:
    proc = run(tmp_path, {"policies": "branch:main", "ruleset_id": "7", "pin": "456"})
    assert proc.returncode == 0, proc.stderr
    assert risk_gate_check(tmp_path) == {"context": "risk-gate", "integration_id": 456}


def test_unpin_needs_the_explicit_switch(tmp_path: Path) -> None:
    files = {"policies": "branch:main", "ruleset_id": "7", "pin": "456"}
    proc = run(tmp_path, files, RISK_GATE_UNPIN="1")
    assert proc.returncode == 0, proc.stderr
    assert risk_gate_check(tmp_path) == {"context": "risk-gate"}


def test_missing_branch_policy_is_created(tmp_path: Path) -> None:
    proc = run(tmp_path, {"policies": "", "ruleset_id": "7"})
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "fake" / "policies").read_text() == "branch:main"


@pytest.mark.parametrize("policies", ["branch:main,branch:feature", "tag:main", "branch:refs/pull/*/merge"])
def test_any_other_branch_policy_stops_the_script(tmp_path: Path, policies: str) -> None:
    proc = run(tmp_path, {"policies": policies, "ruleset_id": "7"}, RISK_GATE_APP_ID="123456")
    assert proc.returncode != 0
    assert not (tmp_path / "fake" / "ruleset.json").exists()


def test_non_numeric_app_id_is_refused(tmp_path: Path) -> None:
    proc = run(tmp_path, {"policies": "branch:main", "ruleset_id": "7"}, RISK_GATE_APP_ID="Iv23abc")
    assert proc.returncode != 0
    assert not (tmp_path / "fake" / "ruleset.json").exists()
