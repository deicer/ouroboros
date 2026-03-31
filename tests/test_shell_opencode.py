import json
import pathlib
import subprocess


def test_build_opencode_cmd_basic():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="edit file")

    assert pathlib.Path(cmd[0]).name == "codex"
    assert cmd[1] == "exec"
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[-1] == "edit file"
    assert "--model" not in cmd


def test_build_opencode_cmd_with_model():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="edit file", model="gpt-5.4")
    assert "-m" in cmd
    model_index = cmd.index("-m")
    assert cmd[model_index + 1] == "gpt-5.4"
    assert cmd[-1] == "edit file"


def test_build_opencode_cmd_places_cd_before_exec():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="edit file", work_dir="/tmp/project")

    assert pathlib.Path(cmd[0]).name == "codex"
    assert cmd[1] == "-C"
    assert cmd[2] == "/tmp/project"
    assert cmd[3] == "exec"
    assert cmd[-1] == "edit file"


def test_build_opencode_cmd_resume_uses_exec_resume():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(prompt="continue edit", session_id="thread-123")

    assert pathlib.Path(cmd[0]).name == "codex"
    assert cmd[1] == "exec"
    assert cmd[2] == "resume"
    assert "--json" in cmd
    assert "thread-123" in cmd
    assert cmd[-1] == "continue edit"


def test_build_opencode_cmd_resume_places_cd_before_exec():
    from ouroboros.tools.shell import _build_opencode_cmd

    cmd = _build_opencode_cmd(
        prompt="continue edit",
        session_id="thread-123",
        work_dir="/tmp/project",
    )

    assert pathlib.Path(cmd[0]).name == "codex"
    assert cmd[1] == "-C"
    assert cmd[2] == "/tmp/project"
    assert cmd[3] == "exec"
    assert cmd[4] == "resume"
    assert "thread-123" in cmd
    assert cmd[-1] == "continue edit"


def test_codex_cli_base_url_derived_from_llm_base(monkeypatch):
    from ouroboros.tools.shell import _codex_cli_base_url

    monkeypatch.delenv("OUROBOROS_CODEX_CLI_BASE_URL", raising=False)
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://31.56.196.40:3455/v1")

    assert _codex_cli_base_url() == "http://31.56.196.40:3455/backend-api/codex"


def test_opencode_prompt_limits_default_to_larger_budget(monkeypatch):
    from ouroboros.tools.shell import _opencode_prompt_limits

    monkeypatch.delenv("OUROBOROS_CODEX_MAX_PROMPT_CHARS", raising=False)
    monkeypatch.delenv("OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", raising=False)
    monkeypatch.delenv("OUROBOROS_CODEX_MAX_PROMPT_LINES", raising=False)
    monkeypatch.delenv("OUROBOROS_OPENCODE_MAX_PROMPT_LINES", raising=False)

    assert _opencode_prompt_limits() == (12000, 300)


def test_opencode_no_changes_detected():
    from ouroboros.tools.shell import _opencode_no_changes_detected

    assert _opencode_no_changes_detected("No changes to apply") is True
    assert _opencode_no_changes_detected("", "Result: no changes to apply") is True
    assert _opencode_no_changes_detected("Updated 1 file", "") is False


def test_opencode_fallback_models_use_only_opencode_env(monkeypatch):
    from ouroboros.tools.shell import _opencode_fallback_models

    monkeypatch.setenv("OUROBOROS_OPENCODE_FALLBACK_MODELS", "")
    monkeypatch.setenv(
        "OUROBOROS_MODEL_FREE_LIST",
        "arcee-ai/trinity-large-preview:free,stepfun/step-3.5-flash:free",
    )
    assert _opencode_fallback_models() == []

    monkeypatch.setenv(
        "OUROBOROS_OPENCODE_FALLBACK_MODELS",
        "opencode/minimax-m2.5-free, opencode/trinity-large-preview-free, opencode/minimax-m2.5-free",
    )
    assert _opencode_fallback_models() == [
        "opencode/minimax-m2.5-free",
        "opencode/trinity-large-preview-free",
    ]


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


def test_extract_opencode_thread_id_from_jsonl():
    from ouroboros.tools.shell import _extract_opencode_thread_id

    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-abc"}),
            json.dumps({"type": "agent", "text": "Patched file"}),
        ]
    )

    assert _extract_opencode_thread_id(output) == "thread-abc"


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


def test_patch_edit_empty_prompt_validation(tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    result = _patch_edit(ctx, "")
    assert result == "⚠️ CODE_EDIT_ARG_ERROR: prompt must be a non-empty string."

    result = _patch_edit(ctx, "   ")
    assert result == "⚠️ CODE_EDIT_ARG_ERROR: prompt must be a non-empty string."

    result = _patch_edit(ctx, 123)  # type: ignore[arg-type]
    assert result == "⚠️ CODE_EDIT_ARG_ERROR: prompt must be a non-empty string."

    result = _patch_edit(ctx, None)  # type: ignore[arg-type]
    assert result == "⚠️ CODE_EDIT_ARG_ERROR: prompt must be a non-empty string."


def test_patch_edit_large_prompt_fast_fails(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)

    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", "500")
    monkeypatch.setenv("OUROBOROS_OPENCODE_MAX_PROMPT_LINES", "20")

    prompt = "\n".join([f"{i}. Refactor block {i}" for i in range(1, 30)])
    result = _patch_edit(ctx, prompt)
    assert result.startswith("⚠️ OPENCODE_PROMPT_TOO_LARGE:")
    assert "Suggested atomic steps:" in result
    assert "1. Refactor block 1" in result


def test_patch_edit_offloads_heavy_direct_chat_to_worker(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    monkeypatch.setenv("OUROBOROS_OPENCODE_OFFLOAD_HEAVY_DIRECT_CHAT", "1")
    ctx = ToolContext(
        repo_dir=repo_dir,
        drive_root=drive_root,
        is_direct_chat=True,
        task_id="parent-1",
    )

    result = _patch_edit(ctx, "Refactor the entire project architecture and split modules by responsibility.")

    assert "HEAVY_PATCH_EDIT_OFFLOADED" in result
    assert any(e.get("type") == "schedule_task" for e in ctx.pending_events)
    evt = next(e for e in ctx.pending_events if e.get("type") == "schedule_task")
    assert evt.get("parent_task_id") == "parent-1"
    assert "patch_edit" in str(evt.get("context") or "")

    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["tool"] == "patch_edit"
    assert last["route"] == "offload_to_worker"
    assert last["ok"] is True


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_patch_edit_fast_path_applies_simple_replace(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

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
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Codex CLI should not be called on fast path")),
    )

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    prompt = (
        "FILE: sample.txt\n"
        "REPLACE: hello\n"
        "WITH: hi\n"
        "COUNT: 1\n"
    )
    result = _patch_edit(ctx, prompt)

    assert "PATCH_EDIT_APPLIED" in result
    assert target.read_text(encoding="utf-8") == "hi world\n"

    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["tool"] == "patch_edit"
    assert last["route"] == "fast_path"
    assert last["ok"] is True


def test_opencode_edit_is_disabled(tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _opencode_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    result = _opencode_edit(ctx, "anything")

    assert "OPENCODE_EDIT_DISABLED" in result

    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["tool"] == "opencode_edit"
    assert last["route"] == "disabled"
    assert last["ok"] is False


def test_parse_fast_edit_prompt_supports_multiline_blocks():
    from ouroboros.tools.shell import _parse_fast_edit_prompt

    prompt = (
        "FILE: app.py\n"
        "REPLACE:\n"
        "def old():\n"
        "    return 1\n"
        "WITH:\n"
        "def new():\n"
        "    return 2\n"
        "COUNT: 1\n"
    )

    parsed = _parse_fast_edit_prompt(prompt)

    assert parsed["file"] == "app.py"
    assert parsed["replace"] == "def old():\n    return 1"
    assert parsed["with"] == "def new():\n    return 2"
    assert parsed["count"] == 1


def test_patch_edit_multiline_fast_path_applies_replace(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")
    target = repo_dir / "sample.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr("ouroboros.tools.shell._run_pytest", lambda repo: "")

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    prompt = (
        "FILE: sample.py\n"
        "REPLACE:\n"
        "def old():\n"
        "    return 1\n"
        "WITH:\n"
        "def new():\n"
        "    return 2\n"
        "COUNT: 1\n"
    )
    result = _patch_edit(ctx, prompt)

    assert "PATCH_EDIT_APPLIED" in result
    assert target.read_text(encoding="utf-8") == "def new():\n    return 2\n"


def test_patch_edit_requires_structured_prompt(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    result = _patch_edit(ctx, "Refactor agent.py")

    assert "PATCH_EDIT_FORMAT" in result
    assert "FILE:" in result


def test_patch_edit_returns_format_error_when_fast_path_fails(tmp_path, monkeypatch):
    from ouroboros.tools import git as git_tools
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.shell import _patch_edit

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    (repo_dir / "sample.txt").write_text("hello world\n", encoding="utf-8")

    monkeypatch.setattr(git_tools, "_acquire_git_lock", lambda ctx: object())
    monkeypatch.setattr(git_tools, "_release_git_lock", lambda lock: None)
    monkeypatch.setattr("ouroboros.tools.shell.run_cmd", lambda *a, **k: "")

    ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
    prompt = (
        "FILE: sample.txt\n"
        "REPLACE: NOT_FOUND\n"
        "WITH: hi\n"
        "COUNT: 1\n"
    )
    result = _patch_edit(ctx, prompt)

    assert "PATCH_EDIT_FORMAT" in result
    stats = _read_jsonl(drive_root / "logs" / "tools_stats.jsonl")
    assert stats
    last = stats[-1]
    assert last["tool"] == "patch_edit"
    assert last["route"] == "patch_failed"
    assert last["ok"] is False


def test_patch_edit_registered_and_opencode_edit_not_public(tmp_path):
    from ouroboros.tools.registry import ToolRegistry

    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    names = {tool["function"]["name"] for tool in registry.schemas()}

    assert "patch_edit" in names
    assert "opencode_edit" not in names
