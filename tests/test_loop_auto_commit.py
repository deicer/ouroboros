from __future__ import annotations

import pathlib

from ouroboros.loop import _process_tool_results


class _FakeTools:
    def __init__(self, *, status: str, commit_result: str) -> None:
        self.status = status
        self.commit_result = commit_result
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict) -> str:
        self.calls.append((name, dict(args)))
        if name == "git_status":
            return self.status
        if name == "repo_commit_push":
            return self.commit_result
        return "OK"


def _exec_result(tool: str, result: str = "OK") -> dict:
    return {
        "tool_call_id": "call-1",
        "fn_name": tool,
        "result": result,
        "is_error": False,
        "args_for_log": {"prompt": "edit file"},
        "is_code_tool": True,
    }


def test_process_tool_results_auto_commit_after_patch_edit(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setenv("OUROBOROS_AUTO_COMMIT_AFTER_EDIT", "1")
    logs = tmp_path / "logs"
    tools = _FakeTools(
        status=" M ouroboros/loop.py",
        commit_result="OK: committed and pushed to ouroboros: auto commit",
    )
    messages: list[dict] = []
    llm_trace = {"tool_calls": []}

    error_count = _process_tool_results(
        results=[_exec_result("patch_edit", result="Updated file")],
        messages=messages,
        llm_trace=llm_trace,
        emit_progress=lambda _m: None,
        drive_logs=logs,
        task_id="t1",
        round_idx=3,
        task_type="task",
        tools=tools,
    )

    assert error_count == 0
    assert [name for name, _ in tools.calls] == ["git_status", "repo_commit_push"]
    commit_msg = tools.calls[1][1]["commit_message"]
    assert "task=t1" in commit_msg
    assert "round=3" in commit_msg
    assert any(item.get("tool") == "repo_commit_push" for item in llm_trace["tool_calls"])


def test_process_tool_results_auto_commit_skips_when_clean(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setenv("OUROBOROS_AUTO_COMMIT_AFTER_EDIT", "1")
    logs = tmp_path / "logs"
    tools = _FakeTools(status="", commit_result="OK")
    messages: list[dict] = []
    llm_trace = {"tool_calls": []}

    error_count = _process_tool_results(
        results=[_exec_result("patch_edit", result="No change")],
        messages=messages,
        llm_trace=llm_trace,
        emit_progress=lambda _m: None,
        drive_logs=logs,
        task_id="t2",
        round_idx=5,
        task_type="task",
        tools=tools,
    )

    assert error_count == 0
    assert [name for name, _ in tools.calls] == ["git_status"]
    assert not any(item.get("tool") == "repo_commit_push" for item in llm_trace["tool_calls"])


def test_process_tool_results_auto_commit_error_increments_error_count(
    monkeypatch,
    tmp_path: pathlib.Path,
):
    monkeypatch.setenv("OUROBOROS_AUTO_COMMIT_AFTER_EDIT", "1")
    logs = tmp_path / "logs"
    tools = _FakeTools(
        status=" M prompts/SYSTEM.md",
        commit_result="⚠️ GIT_ERROR (push): rejected",
    )
    messages: list[dict] = []
    llm_trace = {"tool_calls": []}

    error_count = _process_tool_results(
        results=[_exec_result("patch_edit", result="Applied edits")],
        messages=messages,
        llm_trace=llm_trace,
        emit_progress=lambda _m: None,
        drive_logs=logs,
        task_id="t3",
        round_idx=7,
        task_type="task",
        tools=tools,
    )

    assert error_count == 1
    assert [name for name, _ in tools.calls] == ["git_status", "repo_commit_push"]
