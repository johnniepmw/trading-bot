"""Tests for scripts/secrets-scan.sh with a fake gitleaks on PATH (the real one is exercised by CI)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "secrets-scan.sh"
BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git") or "git"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    for args in (
        ["init", "-q", "-b", "main"],
        ["commit", "-q", "--allow-empty", "-m", "a"],
    ):
        subprocess.run([GIT, *args], cwd=r, check=True, env=env)
    (r / ".gitleaks.toml").write_text("")
    return r


def fake_bin(tmp_path: Path, with_gitleaks: bool) -> Path:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for tool in ("git", "bash", "dirname", "cat"):
        src = shutil.which(tool)
        if src:
            (b / tool).symlink_to(src)
    if with_gitleaks:
        g = b / "gitleaks"
        g.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GITLEAKS_ARGS_OUT"\n')
        g.chmod(0o755)
    return b


def run(repo: Path, tmp_path: Path, *, gitleaks: bool = True, **env: str) -> subprocess.CompletedProcess[str]:
    out = tmp_path / "args.txt"
    full_env = {
        "PATH": str(fake_bin(tmp_path, gitleaks)),
        "GITLEAKS_ARGS_OUT": str(out),
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        **env,
    }
    return subprocess.run(
        [BASH, str(SCRIPT)], cwd=repo, env=full_env, capture_output=True, text=True, check=False
    )


def args(tmp_path: Path) -> list[str]:
    return (tmp_path / "args.txt").read_text().splitlines()


def test_default_range_is_head(repo: Path, tmp_path: Path) -> None:
    proc = run(repo, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "--log-opts=-m HEAD" in args(tmp_path)  # -m: merge commits scanned too
    assert not any("--all" in a for a in args(tmp_path))


def test_range_is_passed_through(repo: Path, tmp_path: Path) -> None:
    proc = run(repo, tmp_path, SECRETS_SCAN_RANGE="HEAD~0..HEAD")
    assert proc.returncode == 0, proc.stderr
    assert "--log-opts=-m HEAD~0..HEAD" in args(tmp_path)


def test_unresolvable_range_fails_closed(repo: Path, tmp_path: Path) -> None:
    proc = run(repo, tmp_path, SECRETS_SCAN_RANGE="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef..HEAD")
    assert proc.returncode != 0
    assert not (tmp_path / "args.txt").exists(), "gitleaks must not run on a bad range"


def test_option_injection_in_range_fails(repo: Path, tmp_path: Path) -> None:
    proc = run(repo, tmp_path, SECRETS_SCAN_RANGE="--all")
    assert proc.returncode != 0
    assert not (tmp_path / "args.txt").exists()


def test_gitleaks_failure_fails_the_scan(repo: Path, tmp_path: Path) -> None:
    b = fake_bin(tmp_path, with_gitleaks=False)
    g = b / "gitleaks"
    g.write_text("#!/bin/sh\nexit 1\n")
    g.chmod(0o755)
    proc = subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=repo,
        env={"PATH": str(b), "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_missing_gitleaks_fails_in_ci(repo: Path, tmp_path: Path) -> None:
    assert run(repo, tmp_path, gitleaks=False, CI="true").returncode != 0


def test_missing_gitleaks_is_a_notice_locally(repo: Path, tmp_path: Path) -> None:
    proc = run(repo, tmp_path, gitleaks=False)
    assert proc.returncode == 0
    assert "not installed" in proc.stdout + proc.stderr
