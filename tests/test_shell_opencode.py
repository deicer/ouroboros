import json
import pathlib


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
