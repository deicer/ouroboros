from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor import events


class _DummyCtx:
    def __init__(self, drive_root: Path, owner_chat_id: int = 123) -> None:
        self.DRIVE_ROOT = drive_root
        self._state = {"owner_chat_id": owner_chat_id}
        self.sent: list[tuple[int, str]] = []
        self.send_calls: list[dict] = []
        self.send_results: list[tuple[bool, int | None]] = []
        self.appended: list[tuple[Path, dict]] = []
        self.enqueued: list[dict] = []
        self.persist_reasons: list[str] = []
        self.budget_updates: list[dict] = []
        self.RUNNING: dict[str, dict] = {}
        self.WORKERS: dict[int, object] = {}
        self.TG = _DummyTG()

    def load_state(self) -> dict:
        return dict(self._state)

    def send_with_budget(self, chat_id: int, text: str, **kwargs: object):
        self.sent.append((chat_id, text))
        self.send_calls.append({"chat_id": chat_id, "text": text, **kwargs})
        if self.send_results:
            return self.send_results.pop(0)
        return True, 101

    def append_jsonl(self, path: Path, payload: dict) -> None:
        self.appended.append((path, payload))

    def enqueue_task(self, task: dict) -> None:
        self.enqueued.append(task)

    def persist_queue_snapshot(self, reason: str) -> None:
        self.persist_reasons.append(reason)

    def update_budget_from_usage(self, usage: dict) -> None:
        self.budget_updates.append(dict(usage))


class _DummyTG:
    def __init__(self) -> None:
        self.reply_calls: list[tuple[int, str, int]] = []
        self.edit_calls: list[tuple[int, int, str]] = []
        self.delete_calls: list[tuple[int, int]] = []
        self.chat_actions: list[tuple[int, str]] = []

    def send_message_reply(self, chat_id: int, text: str, reply_to_message_id: int, parse_mode: str = ""):
        self.reply_calls.append((chat_id, text, reply_to_message_id))
        return True, "ok", 9001

    def edit_message_text(self, chat_id: int, message_id: int, text: str, parse_mode: str = ""):
        self.edit_calls.append((chat_id, message_id, text))
        return True, "ok"

    def delete_message(self, chat_id: int, message_id: int):
        self.delete_calls.append((chat_id, message_id))
        return True, "ok"

    def send_chat_action(self, chat_id: int, action: str = "typing") -> bool:
        self.chat_actions.append((chat_id, action))
        return True


def test_schedule_task_duplicate_rejection_is_silent(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(events, "_find_duplicate_task", lambda *_args, **_kwargs: "dup123")
    ctx = _DummyCtx(tmp_path)

    events._handle_schedule_task({"description": "fix opencode smoke test"}, ctx)

    assert ctx.sent == []
    assert ctx.enqueued == []
    assert len(ctx.appended) == 1
    log_path, payload = ctx.appended[0]
    assert log_path == tmp_path / "logs" / "supervisor.jsonl"
    assert payload["type"] == "schedule_task_duplicate_rejected"
    assert payload["duplicate_task_id"] == "dup123"


def test_schedule_task_enqueues_silent_subtask_without_owner_notification_by_default(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("OUROBOROS_SEND_SCHEDULED_TASK_NOTIFICATIONS", raising=False)
    monkeypatch.setattr(events, "_find_duplicate_task", lambda *_args, **_kwargs: None)
    ctx = _DummyCtx(tmp_path)

    events._handle_schedule_task({"task_id": "t1", "description": "inspect auto-resume"}, ctx)

    assert ctx.sent == []
    assert len(ctx.enqueued) == 1
    assert ctx.enqueued[0]["id"] == "t1"
    assert ctx.enqueued[0]["_silent_subtask"] is True
    assert ctx.persist_reasons == ["schedule_task_event"]


def test_handle_llm_usage_preserves_model_and_cache_fields(tmp_path: Path):
    ctx = _DummyCtx(tmp_path)

    events._handle_llm_usage(
        {
            "ts": "2026-03-15T16:40:00Z",
            "task_id": "task-1",
            "category": "consciousness",
            "model": "gpt-5.4",
            "prompt_tokens": 1200,
            "completion_tokens": 50,
            "cached_tokens": 640,
            "cache_write_tokens": 128,
            "cost": 0.12,
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 50,
                "cached_tokens": 640,
                "cache_write_tokens": 128,
                "cost": 0.12,
            },
        },
        ctx,
    )

    assert ctx.budget_updates[-1]["cached_tokens"] == 640
    log_path = tmp_path / "logs" / "events.jsonl"
    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "llm_usage"
    assert payload["model"] == "gpt-5.4"
    assert payload["prompt_tokens"] == 1200
    assert payload["completion_tokens"] == 50
    assert payload["cached_tokens"] == 640
    assert payload["cache_write_tokens"] == 128
    assert payload["cost"] == 0.12


def test_status_lifecycle_replies_updates_and_deletes(tmp_path: Path):
    events._STATUS_MESSAGES.clear()
    ctx = _DummyCtx(tmp_path)

    events.dispatch_event(
        {
            "type": "status_start",
            "task_id": "task-1",
            "chat_id": 321,
            "reply_to_message_id": 44,
            "text": "thinking...",
        },
        ctx,
    )
    assert ctx.TG.reply_calls
    assert ctx.TG.reply_calls[0][0] == 321
    assert ctx.TG.reply_calls[0][2] == 44

    entry = events._STATUS_MESSAGES["task-1"]
    entry["last_edit_at"] = 0.0
    events.dispatch_event(
        {
            "type": "status_update",
            "task_id": "task-1",
            "chat_id": 321,
            "text": "repo_read, run_shell",
        },
        ctx,
    )
    assert ctx.TG.edit_calls
    assert "repo_read, run_shell" in ctx.TG.edit_calls[-1][2]

    events.dispatch_event(
        {
            "type": "send_message",
            "task_id": "task-1",
            "chat_id": 321,
            "text": "Финальный ответ",
            "reply_to_message_id": 44,
        },
        ctx,
    )
    assert ctx.TG.delete_calls == [(321, 9001)]
    assert ctx.sent[-1] == (321, "Финальный ответ")
    assert "task-1" not in events._STATUS_MESSAGES


def test_final_send_failure_keeps_status_visible(tmp_path: Path):
    events._STATUS_MESSAGES.clear()
    ctx = _DummyCtx(tmp_path)
    ctx.send_results = [(False, None)]

    events.dispatch_event(
        {
            "type": "status_start",
            "task_id": "task-fail",
            "chat_id": 321,
            "reply_to_message_id": 44,
            "text": "thinking...",
        },
        ctx,
    )

    events.dispatch_event(
        {
            "type": "send_message",
            "task_id": "task-fail",
            "chat_id": 321,
            "text": "Финальный ответ",
            "reply_to_message_id": 44,
        },
        ctx,
    )

    assert ctx.TG.delete_calls == []
    assert "task-fail" in events._STATUS_MESSAGES


def test_tick_status_typing_refresh_uses_elapsed_seconds(monkeypatch, tmp_path: Path):
    events._STATUS_MESSAGES.clear()
    ctx = _DummyCtx(tmp_path)

    events.dispatch_event(
        {
            "type": "status_start",
            "task_id": "task-typing",
            "chat_id": 321,
            "reply_to_message_id": 44,
            "text": "thinking...",
        },
        ctx,
    )
    entry = events._STATUS_MESSAGES["task-typing"]
    entry["last_edit_at"] = 0.0
    entry["last_typing_at"] = 100.0

    times = iter([100.5, 100.9, 101.2, 101.5, 105.2])
    monkeypatch.setattr(events.time, "time", lambda: next(times))

    for _ in range(4):
        events.tick_status_animations(ctx)
    assert ctx.TG.chat_actions == []

    events.tick_status_animations(ctx)
    assert ctx.TG.chat_actions == [(321, "typing")]


def test_send_voice_passes_reply_target_to_budget(tmp_path: Path):
    ctx = _DummyCtx(tmp_path)

    events.dispatch_event(
        {
            "type": "send_voice",
            "chat_id": 321,
            "text": "Голосовой ответ",
            "reply_to_message_id": 44,
        },
        ctx,
    )

    assert ctx.send_calls
    assert ctx.send_calls[-1]["reply_to_message_id"] == 44
