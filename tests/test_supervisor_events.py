from __future__ import annotations

import json
from pathlib import Path

from supervisor import events


class _DummyCtx:
    def __init__(self, drive_root: Path, owner_chat_id: int = 123) -> None:
        self.DRIVE_ROOT = drive_root
        self._state = {"owner_chat_id": owner_chat_id}
        self.sent: list[tuple[int, str]] = []
        self.appended: list[tuple[Path, dict]] = []
        self.enqueued: list[dict] = []
        self.persist_reasons: list[str] = []
        self.budget_updates: list[dict] = []

    def load_state(self) -> dict:
        return dict(self._state)

    def send_with_budget(self, chat_id: int, text: str, **_: object) -> None:
        self.sent.append((chat_id, text))

    def append_jsonl(self, path: Path, payload: dict) -> None:
        self.appended.append((path, payload))

    def enqueue_task(self, task: dict) -> None:
        self.enqueued.append(task)

    def persist_queue_snapshot(self, reason: str) -> None:
        self.persist_reasons.append(reason)

    def update_budget_from_usage(self, usage: dict) -> None:
        self.budget_updates.append(dict(usage))


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
