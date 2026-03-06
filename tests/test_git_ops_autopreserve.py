import subprocess


def test_auto_preserve_default_is_disabled(monkeypatch):
    from supervisor.git_ops import _attempt_auto_preserve_unsynced

    monkeypatch.delenv("OUROBOROS_AUTO_PRESERVE_UNSYNCED", raising=False)
    info = _attempt_auto_preserve_unsynced(
        reason="test",
        repo_state={"current_branch": "ouroboros", "dirty_lines": ["?? a.py"], "unpushed_lines": []},
    )
    assert info["enabled"] is False
    assert info["attempted"] is False
    assert info["ok"] is False


def test_auto_preserve_disabled(monkeypatch):
    from supervisor.git_ops import _attempt_auto_preserve_unsynced

    monkeypatch.setenv("OUROBOROS_AUTO_PRESERVE_UNSYNCED", "0")
    info = _attempt_auto_preserve_unsynced(
        reason="test",
        repo_state={"current_branch": "ouroboros", "dirty_lines": ["?? a.py"], "unpushed_lines": []},
    )
    assert info["enabled"] is False
    assert info["attempted"] is False
    assert info["ok"] is False


def test_auto_preserve_dirty_success(monkeypatch):
    from supervisor.git_ops import _attempt_auto_preserve_unsynced

    calls = []

    def fake_run(cmd, cwd=None, check=False, **kwargs):
        calls.append(list(cmd))
        rc = 0
        if cmd[:4] == ["git", "diff", "--cached", "--quiet"]:
            rc = 1  # staged changes exist -> commit needed
        if check and rc != 0:
            raise subprocess.CalledProcessError(returncode=rc, cmd=cmd)
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setenv("OUROBOROS_AUTO_PRESERVE_UNSYNCED", "1")
    monkeypatch.setattr(subprocess, "run", fake_run)

    info = _attempt_auto_preserve_unsynced(
        reason="owner_restart",
        repo_state={"current_branch": "ouroboros", "dirty_lines": ["?? whisper.py"], "unpushed_lines": []},
    )

    assert info["attempted"] is True
    assert info["ok"] is True
    assert info["committed"] is True
    assert info["pushed"] is True
    assert ["git", "commit", "-m", "auto-preserve: unsynced changes before reset (owner_restart)"] in calls
    assert ["git", "push", "origin", "ouroboros"] in calls


def test_auto_preserve_push_failure_rolls_back_temp_commit(monkeypatch):
    from supervisor.git_ops import _attempt_auto_preserve_unsynced

    calls = []

    def fake_run(cmd, cwd=None, check=False, **kwargs):
        calls.append(list(cmd))
        if cmd[:4] == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd[:2] == ["git", "push"]:
            if check:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "push failed")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setenv("OUROBOROS_AUTO_PRESERVE_UNSYNCED", "1")
    monkeypatch.setattr(subprocess, "run", fake_run)

    info = _attempt_auto_preserve_unsynced(
        reason="bootstrap",
        repo_state={"current_branch": "ouroboros", "dirty_lines": ["?? new_tool.py"], "unpushed_lines": []},
    )

    assert info["attempted"] is True
    assert info["ok"] is False
    assert info["committed"] is True
    assert info["pushed"] is False
    assert ["git", "reset", "HEAD~1"] in calls
