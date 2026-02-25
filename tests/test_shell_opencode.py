import json
import pathlib
import subprocess


def test_build_opencode_cmd_basic():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="edit file")

    assert pathlib.Path(cmd[0]).name == "opencode"
    assert cmd[1] == "run"
    assert cmd[2] == "edit file"
    assert "--format" in cmd
    assert "json" in cmd
    assert "--model" not in cmd


def test_build_opencode_cmd_with_model():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="edit file", model="opencode/minimax-m2.5-free")
    assert cmd[2] == "-m"
    assert cmd[3] == "opencode/minimax-m2.5-free"
    assert cmd[4] == "edit file"


def test_opencode_no_changes_detected():
    from ouroboros.tools.shell import _opencode_no_changes_detected

    assert _opencode_no_changes_detected("No changes to apply") is True
    assert _opencode_no_changes_detected("", "Result: no changes to apply") is True
    assert _opencode_no_changes_detected("Updated 1 file", "") is False


def test_opencode_prompt_too_large_and_step_extraction(monkeypatch):
    from ouroboros.tools.shell import _extract_atomic_steps, _opencode_prompt_too_large

    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", "500")
    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_LINES", "20")

    prompt = "\n".join([f"{i}. Step {i} for refactor" for i in range(1, 25)])
    too_large, char_count, line_count, max_chars, max_lines = _opencode_prompt_too_large(prompt)
    assert too_large is True
    assert line_count > max_lines

    steps = _extract_atomic_steps(prompt)
    assert steps[0] == "Step 1 for refactor"
    assert steps[1] == "Step 2 for refactor"
    assert steps[2] == "Step 3 for refactor"


def test_parse_opencode_output_dict_payload():
    from ouroboros.tools.shell import _parse_opencode_output

    payload = {"text": "Done", "cost": 0.12}
    result = _parse_opencode_output(json.dumps(payload))

    assert "Done" in result


def test_parse_opencode_output_jsonl_events():
    from ouroboros.tools.shell import _parse_opencode_output

    output = "\n".join(
        [
            json.dumps({"type": "agent", "text": "Step 1"}),
            json.dumps({"type": "agent", "text": "Step 2"}),
        ]
    )
    result = _parse_opencode_output(output)
    assert "Step 1" in result
    assert "Step 2" in result


def test_opencode_error_payload_detection():
    from ouroboros.tools.shell import _opencode_has_error_payload

    ok = json.dumps({"type": "text", "text": "OK"})
    err = json.dumps({"type": "error", "error": {"message": "bad"}})

    assert _opencode_has_error_payload(ok) is False
    assert _opencode_has_error_payload(err) is True


def test_copilot_reauth_detection():
    from ouroboros.tools.shell import _is_copilot_reauth_error

    assert _is_copilot_reauth_error(
        stdout="Please reauthenticate with the copilot provider",
        stderr="",
    ) is True
    assert _is_copilot_reauth_error(
        stdout="",
        stderr="https://api.githubcopilot.com/chat/completions",
    ) is True
    assert _is_copilot_reauth_error(stdout="other error", stderr="") is False


def test_opencode_edit_empty_prompt_validation(tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    result = _opencode_edit(ctx, "")
    assert result == "⚠️ OPENCODE_ARG_ERROR: prompt must be a non-empty string."

    result = _opencode_edit(ctx, "   ")
    assert result == "⚠️ OPENCODE_ARG_ERROR: prompt must be a non-empty string."

    result = _opencode_edit(ctx, 123)  # type: ignore[arg-type]
    assert result == "⚠️ OPENCODE_ARG_ERROR: prompt must be a non-empty string."

    result = _opencode_edit(ctx, None)  # type: ignore[arg-type]
    assert result == "⚠️ OPENCODE_ARG_ERROR: prompt must be a non-empty string."


def test_opencode_edit_large_prompt_fast_fails(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", "500")
    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_LINES", "20")

    prompt = "\n".join([f"{i}. Refactor block {i}" for i in range(1, 30)])
    result = _opencode_edit(ctx, prompt)
    assert result.startswith("⚠️ OPENCODE_PROMPT_TOO_LARGE:")
    assert "Suggested atomic steps:" in result
    assert "1. Refactor block 1" in result


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_opencode_edit_fast_path_applies_simple_replace(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    target = repo_dir / "sample.txt"
    target.write_text("hello world\n", encoding="utf-8")

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")
    monkeypatch.setattr("ouroboros.tools.shell._run_pytest", lambda repo: "")
    monkeypatch.setattr(
        "ouroboros.tools.shell._run_opencode_cli",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("OpenCode should not be called on fast path")),
    )

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    prompt = (
        "FILE: sample.txt\n"
        "REPLACE: hello\n"
        "WITH: hi\n"
        "COUNT: 1\n"
    )
    result = _opencode_edit(ctx, prompt)

    assert "FAST_EDIT_APPLIED" in result
    assert target.read_text(encoding="utf-8") == "hi world\n"

    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["tool"] == "opencode_edit"
    assert last["route"] == "fast_path"
    assert last["ok"] is True


def test_opencode_edit_fast_path_falls_back_to_opencode(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    (repo_dir / "sample.txt").write_text("hello world\n", encoding="utf-8")

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")
    monkeypatch.setattr("ouroboros.tools.shell._run_pytest", lambda repo: "")
    monkeypatch.setenv("OUROBOROS_OPENCODE_FALLBACK_MODELS", "")
    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_RETRIES", "1")
    monkeypatch.setattr(
        "ouroboros.tools.shell._run_opencode_cli",
        lambda **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout='{"text":"patched"}', stderr=""),
    )

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    prompt = (
        "FILE: sample.txt\n"
        "REPLACE: NOT_FOUND\n"
        "WITH: hi\n"
        "COUNT: 1\n"
    )
    result = _opencode_edit(ctx, prompt)

    assert "patched" in result
    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["route"] == "fallback_to_opencode"
    assert last["fallback_used"] is True
    assert last["ok"] is True
    assert int(last["attempts_total"]) >= 1


def test_opencode_edit_logs_failure_stats(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")
    monkeypatch.setattr("ouroboros.tools.shell._run_pytest", lambda repo: "")
    monkeypatch.setenv("OUROBOROS_OPENCODE_FALLBACK_MODELS", "")
    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_RETRIES", "1")
    monkeypatch.setattr(
        "ouroboros.tools.shell._run_opencode_cli",
        lambda **kwargs: subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom"),
    )

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    result = _opencode_edit(ctx, "Refactor agent.py")

    assert result.startswith("⚠️ OPENCODE_ERROR:")
    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["route"] == "opencode"
    assert last["ok"] is False
    assert last["failure_reason"] == "opencode_failed"
    assert int(last["attempts_total"]) >= 1


def test_ensure_opencode_cli_disabled_auto_install(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _ensure_opencode_cli

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    monkeypatch.setenv("OUROBOROS_OPENCODE_AUTO_INSTALL", "0")
    monkeypatch.setattr("ouroboros.tools.shell.shutil.which", lambda *args, **kwargs: None)

    ok, info = _ensure_opencode_cli(ctx, work_dir=str(repo_dir), env={"PATH": "/usr/bin"})
    assert ok is False
    assert "auto-install is disabled" in info


def test_opencode_edit_fails_fast_when_bootstrap_unavailable(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")
    monkeypatch.setenv("OUROBOROS_OPENCODE_AUTO_INSTALL", "0")
    monkeypatch.setattr("ouroboros.tools.shell.shutil.which", lambda *args, **kwargs: None)

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    result = _opencode_edit(ctx, "Refactor agent.py")

    assert result.startswith("⚠️ OPENCODE_BOOTSTRAP_FAILED:")
    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["ok"] is False
    assert last["failure_reason"] == "opencode_bootstrap_failed"
