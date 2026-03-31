import os
import pathlib
import shutil
import time


def _mk_ctx(tmp_path):
    from ouroboros.tools.registry import ToolContext

    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    drive_root.mkdir()
    (repo_dir / ".git").mkdir()
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def test_ensure_git_repo_ready_auto_recovers_rebase_and_stale_lock(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    ctx = _mk_ctx(tmp_path)
    git_dir = ctx.repo_dir / ".git"
    (git_dir / "rebase-merge").mkdir()
    lock_path = git_dir / "index.lock"
    lock_path.write_text("locked", encoding="utf-8")
    old = time.time() - 600
    os.utime(lock_path, (old, old))

    calls = []

    def fake_capture_git(_repo_dir, cmd):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return 0, "HEAD", ""
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"]:
            if (git_dir / "rebase-merge").exists():
                return 0, "docker-compose.yml\nouroboros/bootstrap_env.py\n", ""
            return 0, "", ""
        return 0, "", ""

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ["git", "rebase", "--abort"]:
            shutil.rmtree(git_dir / "rebase-merge")
            return ""
        raise AssertionError(f"unexpected run_cmd call: {cmd!r}")

    monkeypatch.setattr(git_tools, "_capture_git", fake_capture_git)
    monkeypatch.setattr(git_tools, "run_cmd", fake_run_cmd)
    monkeypatch.setenv("OUROBOROS_GIT_INDEX_LOCK_STALE_SEC", "30")

    ok, note = git_tools._ensure_git_repo_ready(ctx, action="repo_commit_push")

    assert ok is True
    assert ["git", "rebase", "--abort"] in calls
    assert not lock_path.exists()
    assert "auto-recovered" in note


def test_repo_commit_push_returns_repo_unhealthy_without_checkout(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    ctx = _mk_ctx(tmp_path)

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda _ctx: tmp_path / "git.lock")
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda _lock: None)
    monkeypatch.setattr(
        git_tools,
        "_ensure_git_repo_ready",
        lambda _ctx, action="repo_commit_push", auto_recover=True: (
            False,
            "⚠️ GIT_REPO_UNHEALTHY: rebase in progress; unmerged files: docker-compose.yml",
        ),
    )

    calls = []
    monkeypatch.setattr(git_tools, "run_cmd", lambda cmd, cwd=None: calls.append(cmd) or "")

    result = git_tools._repo_commit_push(ctx, "test commit")

    assert result.startswith("⚠️ GIT_REPO_UNHEALTHY:")
    assert calls == []


def test_patch_edit_returns_repo_unhealthy_without_checkout(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda _ctx: tmp_path / "git.lock")
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda _lock: None)
    monkeypatch.setattr(
        git_tools,
        "_ensure_git_repo_ready",
        lambda _ctx, action="patch_edit", auto_recover=True: (
            False,
            "⚠️ GIT_REPO_UNHEALTHY: interactive rebase in progress",
        ),
    )

    calls = []
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda cmd, cwd=None: calls.append(cmd) or "")

    result = _patch_edit(ctx, "Refactor agent.py")

    assert result.startswith("⚠️ GIT_REPO_UNHEALTHY:")
    assert calls == []


def test_git_repo_health_reports_unhealthy_state(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    ctx = _mk_ctx(tmp_path)
    monkeypatch.setattr(
        git_tools,
        "_inspect_git_repo_state",
        lambda repo_dir: {
            "branch": "HEAD",
            "unmerged_files": ["docker-compose.yml", "ouroboros/bootstrap_env.py"],
            "rebase_in_progress": True,
            "merge_in_progress": False,
            "cherry_pick_in_progress": False,
            "revert_in_progress": False,
            "index_lock_exists": True,
            "index_lock_age_sec": 300.0,
        },
    )

    result = git_tools._git_repo_health(ctx)

    assert "healthy: no" in result
    assert "rebase in progress" in result
    assert "docker-compose.yml" in result


def test_git_repo_health_auto_recover_reports_post_state(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    ctx = _mk_ctx(tmp_path)
    states = [
        {
            "branch": "HEAD",
            "unmerged_files": ["docker-compose.yml"],
            "rebase_in_progress": True,
            "merge_in_progress": False,
            "cherry_pick_in_progress": False,
            "revert_in_progress": False,
            "index_lock_exists": False,
            "index_lock_age_sec": 0.0,
        },
        {
            "branch": "ouroboros",
            "unmerged_files": [],
            "rebase_in_progress": False,
            "merge_in_progress": False,
            "cherry_pick_in_progress": False,
            "revert_in_progress": False,
            "index_lock_exists": False,
            "index_lock_age_sec": 0.0,
        },
    ]
    monkeypatch.setattr(git_tools, "_inspect_git_repo_state", lambda repo_dir: states.pop(0))
    monkeypatch.setattr(
        git_tools,
        "_ensure_git_repo_ready",
        lambda _ctx, action="git_repo_health", auto_recover=True: (
            True,
            "⚠️ GIT_REPO_AUTO_RECOVERED: auto-recovered before git_repo_health: aborted rebase",
        ),
    )

    result = git_tools._git_repo_health(ctx, auto_recover=True)

    assert "recovery_ok: yes" in result
    assert "post_state: clean" in result
