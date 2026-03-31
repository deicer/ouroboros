from __future__ import annotations

import pathlib
import subprocess

from ouroboros.agent import Env, OuroborosAgent


def _mk_env(tmp_path: pathlib.Path) -> Env:
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    (repo_dir / ".git").mkdir(parents=True)
    (drive_root / "logs").mkdir(parents=True)
    return Env(repo_dir=repo_dir, drive_root=drive_root)


def test_startup_uncommitted_changes_skip_remote_sync_by_default(monkeypatch, tmp_path):
    env = _mk_env(tmp_path)
    calls = []

    def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None, check=False):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, " M README.md\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.delenv("OUROBOROS_STARTUP_AUTO_RESCUE_PUSH", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    info, issues = OuroborosAgent._check_uncommitted_changes(type("Dummy", (), {"env": env})())

    assert issues == 1
    assert info["status"] == "warning"
    assert info["auto_committed"] is True
    assert ["git", "pull", "--rebase", "origin", env.branch_dev] not in calls
    assert ["git", "push", "origin", env.branch_dev] not in calls


def test_startup_uncommitted_changes_can_enable_remote_sync(monkeypatch, tmp_path):
    env = _mk_env(tmp_path)
    calls = []

    def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None, check=False):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, " M README.md\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setenv("OUROBOROS_STARTUP_AUTO_RESCUE_PUSH", "1")
    monkeypatch.setattr(subprocess, "run", fake_run)

    info, issues = OuroborosAgent._check_uncommitted_changes(type("Dummy", (), {"env": env})())

    assert issues == 1
    assert info["status"] == "warning"
    assert info["auto_committed"] is True
    assert ["git", "pull", "--rebase", "origin", env.branch_dev] in calls
    assert ["git", "push", "origin", env.branch_dev] in calls


def test_startup_uncommitted_changes_skips_auto_rescue_when_repo_unhealthy(monkeypatch, tmp_path):
    env = _mk_env(tmp_path)
    calls = []

    def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None, check=False):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, " M README.md\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.delenv("OUROBOROS_STARTUP_AUTO_RESCUE_PUSH", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "ouroboros.agent._ensure_git_repo_ready",
        lambda ctx, action="startup auto-rescue", auto_recover=True: (
            False,
            "⚠️ GIT_REPO_UNHEALTHY: interactive rebase in progress",
        ),
    )
    monkeypatch.setattr("ouroboros.agent._acquire_git_lock", lambda ctx, timeout_sec=120: tmp_path / "git.lock")
    monkeypatch.setattr("ouroboros.agent._release_git_lock", lambda lock: None)

    info, issues = OuroborosAgent._check_uncommitted_changes(type("Dummy", (), {"env": env})())

    assert issues == 1
    assert info["status"] == "warning"
    assert info["auto_committed"] is False
    assert info["note"] == "⚠️ GIT_REPO_UNHEALTHY: interactive rebase in progress"
    assert ["git", "add", "-A"] not in calls
