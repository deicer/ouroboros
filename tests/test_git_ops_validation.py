import pathlib
import subprocess


def test_run_runtime_validation_stops_on_first_failure(monkeypatch, tmp_path: pathlib.Path):
    from supervisor import git_ops

    calls = []

    def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None, check=False):
        calls.append(list(cmd))
        if cmd[:5] == [git_ops.sys.executable, "-m", "ruff", "check", "--select=E,F"]:
            return subprocess.CompletedProcess(cmd, 1, "", "F821 boom")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(git_ops, "REPO_DIR", pathlib.Path("/tmp/repo"))
    monkeypatch.setattr(git_ops, "DRIVE_ROOT", tmp_path)

    out = git_ops._run_runtime_validation(branch_name="ouroboros", reason="test")

    assert out["ok"] is False
    assert out["failed_step"] == "ruff_ef"
    assert [step["name"] for step in out["steps"]] == ["compileall", "ruff_ef"]
    assert all(c[:2] == [git_ops.sys.executable, "-m"] for c in calls)


def test_safe_restart_fallbacks_when_dev_validation_fails(monkeypatch):
    from supervisor import git_ops

    logs = []
    validations = []

    monkeypatch.setattr(git_ops, "BRANCH_DEV", "ouroboros")
    monkeypatch.setattr(git_ops, "BRANCH_STABLE", "ouroboros-stable")
    monkeypatch.setattr(git_ops, "checkout_and_reset", lambda branch, reason="", unsynced_policy="": (True, "ok"))
    monkeypatch.setattr(git_ops, "sync_runtime_dependencies", lambda reason: (True, "ok"))
    monkeypatch.setattr(git_ops, "import_test", lambda: {"ok": True, "stdout": "import_ok", "stderr": "", "returncode": 0})
    monkeypatch.setattr(git_ops, "git_capture", lambda cmd: (0, "", ""))  # no stable tags -> stable branch fallback
    monkeypatch.setattr(git_ops, "append_jsonl", lambda p, o: logs.append(dict(o)))

    def fake_validate(branch_name: str, reason: str):
        validations.append((branch_name, reason))
        if branch_name == "ouroboros":
            return {"ok": False, "failed_step": "pytest", "steps": [{"name": "pytest"}]}
        return {"ok": True, "failed_step": "", "steps": [{"name": "pytest"}]}

    monkeypatch.setattr(git_ops, "_run_runtime_validation", fake_validate)

    ok, msg = git_ops.safe_restart(reason="owner_restart")
    assert ok is True
    assert "fell back to ouroboros-stable" in msg
    assert validations[0][0] == "ouroboros"
    assert validations[-1][0] == "ouroboros-stable"
    assert any(row.get("type") == "safe_restart_dev_validation_failed" for row in logs)


def test_safe_restart_dev_success_with_runtime_validation(monkeypatch):
    from supervisor import git_ops

    monkeypatch.setattr(git_ops, "BRANCH_DEV", "ouroboros")
    monkeypatch.setattr(git_ops, "checkout_and_reset", lambda branch, reason="", unsynced_policy="": (True, "ok"))
    monkeypatch.setattr(git_ops, "sync_runtime_dependencies", lambda reason: (True, "ok"))
    monkeypatch.setattr(git_ops, "import_test", lambda: {"ok": True, "stdout": "import_ok", "stderr": "", "returncode": 0})
    monkeypatch.setattr(
        git_ops,
        "_run_runtime_validation",
        lambda branch_name, reason: {"ok": True, "failed_step": "", "steps": [{"name": "pytest"}]},
    )

    ok, msg = git_ops.safe_restart(reason="owner_restart")
    assert ok is True
    assert msg == "OK: ouroboros"
