from __future__ import annotations

from ouroboros.loop import (
    _assistant_content_for_history,
    _call_llm_with_retry,
    _setup_dynamic_tools,
    _truncate_tool_result,
)


class _DummyRegistry:
    def __init__(self) -> None:
        self.handlers = {}
        self.queued = []

    def list_non_core_tools(self):
        return [{"name": "extra_tool", "description": "extra desc"}]

    def get_schema_by_name(self, name: str):
        if name == "extra_tool":
            return {"type": "function", "function": {"name": "extra_tool", "description": "extra desc"}}
        return None

    def override_handler(self, name: str, handler) -> None:
        self.handlers[name] = handler

    def queue_tools_for_next_task(self, names):
        self.queued.extend(names)
        return list(names)


def test_setup_dynamic_tools_queues_enablement_for_next_task_without_mutating_current_toolset():
    registry = _DummyRegistry()
    tool_schemas = [{"type": "function", "function": {"name": "repo_read"}}]
    messages = []

    updated_schemas, _enabled = _setup_dynamic_tools(registry, tool_schemas, messages)
    result = registry.handlers["enable_tools"](tools="extra_tool")

    assert updated_schemas == tool_schemas
    assert len(updated_schemas) == 1
    assert registry.queued == ["extra_tool"]
    assert "next task" in result.lower()
    assert "extra_tool" in result


def test_call_llm_with_retry_passes_prompt_cache_key(monkeypatch, tmp_path):
    captured = {}

    class DummyLLM:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return {"content": "ok", "tool_calls": []}, {"prompt_tokens": 12, "completion_tokens": 1}

    msg, _ = _call_llm_with_retry(
        llm=DummyLLM(),
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.4",
        tools=None,
        effort="medium",
        max_retries=1,
        drive_logs=tmp_path,
        task_id="task-cache",
        round_idx=1,
        event_queue=None,
        accumulated_usage={},
        task_type="task",
        prompt_cache_key="ouroboros:test-key",
    )

    assert msg["content"] == "ok"
    assert captured["prompt_cache_key"] == "ouroboros:test-key"


def test_call_llm_with_retry_passes_session_id(monkeypatch, tmp_path):
    captured = {}

    class DummyLLM:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return {"content": "ok", "tool_calls": []}, {"prompt_tokens": 12, "completion_tokens": 1}

    msg, _ = _call_llm_with_retry(
        llm=DummyLLM(),
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.4",
        tools=None,
        effort="medium",
        max_retries=1,
        drive_logs=tmp_path,
        task_id="task-cache",
        round_idx=1,
        event_queue=None,
        accumulated_usage={},
        task_type="task",
        prompt_cache_key="sess-task-cache",
        session_id="sess-task-cache",
    )

    assert msg["content"] == "ok"
    assert captured["prompt_cache_key"] == "sess-task-cache"
    assert captured["session_id"] == "sess-task-cache"


def test_assistant_tool_call_history_drops_progress_text_for_cache_stability():
    assert _assistant_content_for_history(
        "Сначала проверю файл, потом внесу правку.",
        [{"id": "call-1", "function": {"name": "repo_read", "arguments": "{\"path\":\"README.md\"}"}}],
    ) == ""
    assert _assistant_content_for_history("Финальный ответ", []) == "Финальный ответ"


def test_truncate_tool_result_uses_tighter_caps_for_noisy_tools():
    noisy = "x" * 5000
    repo_read = "y" * 5000

    patch_out = _truncate_tool_result(noisy, tool_name="patch_edit")
    repo_read_out = _truncate_tool_result(repo_read, tool_name="repo_read")

    assert len(patch_out) < len(repo_read_out)
    assert "... (truncated from 5000 chars)" in patch_out


def test_call_llm_with_retry_accumulates_estimated_cost_when_usage_omits_cost(monkeypatch, tmp_path):
    accumulated_usage = {}

    class DummyLLM:
        def chat(self, **kwargs):
            return {"content": "ok", "tool_calls": []}, {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
            }

    monkeypatch.setattr("ouroboros.loop._estimate_cost", lambda *args, **kwargs: 0.42)

    msg, cost = _call_llm_with_retry(
        llm=DummyLLM(),
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.4",
        tools=None,
        effort="medium",
        max_retries=1,
        drive_logs=tmp_path,
        task_id="task-cost",
        round_idx=1,
        event_queue=None,
        accumulated_usage=accumulated_usage,
        task_type="task",
    )

    assert msg["content"] == "ok"
    assert cost == 0.42
    assert accumulated_usage["cost"] == 0.42
