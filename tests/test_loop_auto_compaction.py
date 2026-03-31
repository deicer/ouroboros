import pathlib
from types import SimpleNamespace

from ouroboros import loop


class _DummyTools:
    def __init__(self):
        self._ctx = SimpleNamespace()
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "compact_context":
            self._ctx._pending_compaction = int(args.get("keep_last_n", 6))
            return "scheduled"
        return "ok"


def test_auto_compact_triggers_with_auto_flag_and_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("OUROBOROS_AUTO_CONTEXT_COMPACT", "true")
    monkeypatch.setenv("OUROBOROS_MODEL_CONTEXT_WINDOW", "100")
    monkeypatch.setenv("OUROBOROS_AUTO_CONTEXT_COMPACT_AT_PCT", "70")
    monkeypatch.setenv("OUROBOROS_AUTO_CONTEXT_COMPACT_COOLDOWN_ROUNDS", "2")

    tools = _DummyTools()
    messages = [
        {"role": "user", "content": "x" * 600},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a1", "function": {"name": "repo_read", "arguments": "{\"path\":\"README.md\"}"}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "y" * 600},
    ]

    marker_msg = {"role": "system", "content": "compacted"}

    def _fake_compact(msgs, keep_recent):
        assert keep_recent == 6
        return list(msgs) + [marker_msg]

    monkeypatch.setattr(loop, "compact_tool_history_llm", _fake_compact)

    out = loop._maybe_auto_compact_context(
        round_idx=10,
        messages=messages,
        tools=tools,
        active_model="x-ai/grok-4.1-fast",
        drive_logs=pathlib.Path(tmp_path) / "logs",
        task_id="t-1",
        task_type="task",
        emit_progress=lambda _msg: None,
    )

    assert out[-1] == marker_msg
    assert tools.calls == [("compact_context", {"keep_last_n": 6, "auto": True})]
    assert getattr(tools._ctx, "_last_auto_compact_round", None) == 10

    # Next round is inside cooldown => no second call.
    out2 = loop._maybe_auto_compact_context(
        round_idx=11,
        messages=out,
        tools=tools,
        active_model="x-ai/grok-4.1-fast",
        drive_logs=pathlib.Path(tmp_path) / "logs",
        task_id="t-1",
        task_type="task",
        emit_progress=lambda _msg: None,
    )
    assert out2 == out
    assert len(tools.calls) == 1


def test_auto_compact_skips_when_below_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("OUROBOROS_AUTO_CONTEXT_COMPACT", "true")
    monkeypatch.setenv("OUROBOROS_MODEL_CONTEXT_WINDOW", "10000")
    monkeypatch.setenv("OUROBOROS_AUTO_CONTEXT_COMPACT_AT_PCT", "70")

    tools = _DummyTools()
    messages = [{"role": "user", "content": "short text"}]

    out = loop._maybe_auto_compact_context(
        round_idx=3,
        messages=messages,
        tools=tools,
        active_model="anthropic/claude-sonnet-4.6",
        drive_logs=pathlib.Path(tmp_path) / "logs",
        task_id="t-2",
        task_type="task",
        emit_progress=lambda _msg: None,
    )

    assert out == messages
    assert tools.calls == []


def test_history_compaction_does_not_trigger_only_from_round_count(monkeypatch):
    messages = [
        {"role": "user", "content": "short"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a1", "function": {"name": "repo_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "result"},
    ]

    monkeypatch.setattr(loop, "compact_tool_history", lambda msgs, keep_recent=6: list(msgs) + [{"role": "system", "content": "compacted"}])

    out = loop._maybe_compact_history_messages(messages=messages, round_idx=12, pending_keep_recent=None)

    assert out == messages


def test_history_compaction_triggers_for_long_message_list(monkeypatch):
    messages = [{"role": "user", "content": f"m{i}"} for i in range(61)]
    marker = {"role": "system", "content": "compacted"}

    monkeypatch.setattr(loop, "compact_tool_history", lambda msgs, keep_recent=6: list(msgs) + [marker])

    out = loop._maybe_compact_history_messages(messages=messages, round_idx=4, pending_keep_recent=None)

    assert out[-1] == marker
