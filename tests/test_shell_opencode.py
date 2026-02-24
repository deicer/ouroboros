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
