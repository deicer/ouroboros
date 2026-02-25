import pathlib
import subprocess


def test_sync_runtime_dependencies_ignores_optional_failures_when_not_strict(monkeypatch, tmp_path: pathlib.Path):
    from supervisor import git_ops

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
    monkeypatch.setattr(git_ops, "REPO_DIR", repo_dir)
    monkeypatch.setattr(git_ops, "DRIVE_ROOT", tmp_path)
    monkeypatch.setenv("OUROBOROS_RUNTIME_EXTRA_PIP", "okpkg,badpkg")
    monkeypatch.delenv("OUROBOROS_RUNTIME_EXTRA_PIP_STRICT", raising=False)

    calls = []
    events = []

    def fake_run(cmd, cwd=None, check=False, **kwargs):
        calls.append(list(cmd))
        joined = " ".join(str(x) for x in cmd)
        if " badpkg" in f" {joined}":
            if check:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "bad")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(git_ops, "append_jsonl", lambda p, o: events.append(dict(o)))

    ok, msg = git_ops.sync_runtime_dependencies(reason="test")
    assert ok is True
    assert "requirements:" in msg
    assert any(ev.get("type") == "deps_sync_extra_error" and ev.get("package") == "badpkg" for ev in events)
    assert any(ev.get("type") == "deps_sync_ok" for ev in events)
    assert any("okpkg" in " ".join(map(str, cmd)) for cmd in calls)


def test_sync_runtime_dependencies_fails_when_optional_failures_strict(monkeypatch, tmp_path: pathlib.Path):
    from supervisor import git_ops

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
    monkeypatch.setattr(git_ops, "REPO_DIR", repo_dir)
    monkeypatch.setattr(git_ops, "DRIVE_ROOT", tmp_path)
    monkeypatch.setenv("OUROBOROS_RUNTIME_EXTRA_PIP", "badpkg")
    monkeypatch.setenv("OUROBOROS_RUNTIME_EXTRA_PIP_STRICT", "1")

    events = []

    def fake_run(cmd, cwd=None, check=False, **kwargs):
        joined = " ".join(str(x) for x in cmd)
        if " badpkg" in f" {joined}" and check:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(git_ops, "append_jsonl", lambda p, o: events.append(dict(o)))

    ok, msg = git_ops.sync_runtime_dependencies(reason="test")
    assert ok is False
    assert "Optional deps failed (strict)" in msg
    assert any(ev.get("type") == "deps_sync_extra_error" for ev in events)
