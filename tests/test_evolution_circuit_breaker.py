import json
import pathlib
from types import SimpleNamespace

from supervisor import queue
from supervisor.events import _handle_task_done, _handle_toggle_evolution


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_enqueue_evolution_logs_enqueued(monkeypatch, tmp_path: pathlib.Path):
    state = {
        "evolution_mode_enabled": True,
        "owner_chat_id": 101,
        "evolution_cycle": 7,
        "evolution_consecutive_failures": 0,
    }
    saved: dict = {}
    enqueued: list[dict] = []
    logs: list[tuple[pathlib.Path, dict]] = []

    monkeypatch.setattr(queue, "DRIVE_ROOT", tmp_path)
    monkeypatch.setattr(queue, "PENDING", [])
    monkeypatch.setattr(queue, "RUNNING", {})
    monkeypatch.setattr(queue, "load_state", lambda: dict(state))
    monkeypatch.setattr(queue, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr(queue, "send_with_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(queue, "enqueue_task", lambda task, front=False: enqueued.append(dict(task)) or task)
    monkeypatch.setattr(queue, "append_jsonl", lambda p, o: logs.append((p, dict(o))))

    queue.enqueue_evolution_task_if_needed()

    assert len(enqueued) == 1
    assert enqueued[0]["type"] == "evolution"
    assert saved["evolution_cycle"] == 8
    evolution_logs = [obj for path, obj in logs if path.name == "evolution_log.jsonl"]
    assert any(obj.get("type") == "evolution_enqueued" for obj in evolution_logs)


def test_enqueue_evolution_logs_breaker_open(monkeypatch, tmp_path: pathlib.Path):
    state = {
        "evolution_mode_enabled": True,
        "owner_chat_id": 101,
        "evolution_cycle": 1,
        "evolution_consecutive_failures": 3,
    }
    saved: dict = {}
    logs: list[tuple[pathlib.Path, dict]] = []

    monkeypatch.setattr(queue, "DRIVE_ROOT", tmp_path)
    monkeypatch.setattr(queue, "PENDING", [])
    monkeypatch.setattr(queue, "RUNNING", {})
    monkeypatch.setattr(queue, "load_state", lambda: dict(state))
    monkeypatch.setattr(queue, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr(queue, "send_with_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(queue, "append_jsonl", lambda p, o: logs.append((p, dict(o))))

    queue.enqueue_evolution_task_if_needed()

    assert saved["evolution_mode_enabled"] is False
    evolution_logs = [obj for path, obj in logs if path.name == "evolution_log.jsonl"]
    breaker = [obj for obj in evolution_logs if obj.get("type") == "evolution_breaker_open"]
    assert len(breaker) == 1
    assert breaker[0]["consecutive_failures"] == 3


def test_enqueue_evolution_does_not_stop_on_low_budget(monkeypatch, tmp_path: pathlib.Path):
    state = {
        "evolution_mode_enabled": True,
        "owner_chat_id": 101,
        "evolution_cycle": 5,
        "evolution_consecutive_failures": 0,
    }
    saved: dict = {}
    logs: list[tuple[pathlib.Path, dict]] = []

    monkeypatch.setattr(queue, "DRIVE_ROOT", tmp_path)
    monkeypatch.setattr(queue, "PENDING", [])
    monkeypatch.setattr(queue, "RUNNING", {})
    monkeypatch.setattr(queue, "load_state", lambda: dict(state))
    monkeypatch.setattr(queue, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr(queue, "send_with_budget", lambda *args, **kwargs: None)
    enqueued: list[dict] = []
    monkeypatch.setattr(queue, "enqueue_task", lambda task, front=False: enqueued.append(dict(task)) or task)
    monkeypatch.setattr(queue, "append_jsonl", lambda p, o: logs.append((p, dict(o))))

    queue.enqueue_evolution_task_if_needed()

    assert len(enqueued) == 1
    assert enqueued[0]["type"] == "evolution"
    assert saved["evolution_mode_enabled"] is True
    evolution_logs = [obj for path, obj in logs if path.name == "evolution_log.jsonl"]
    stop = [obj for obj in evolution_logs if obj.get("type") == "evolution_budget_stop"]
    assert stop == []


class _DummyCtx:
    def __init__(self, root: pathlib.Path):
        self.DRIVE_ROOT = root
        self.REPO_DIR = root
        self.RUNNING = {"evo-1": {"task": {"type": "evolution"}, "started_at": 1.0}}
        self.WORKERS = {1: SimpleNamespace(busy_task_id="evo-1")}
        self.PENDING = []
        self._state = {
            "owner_chat_id": 101,
            "evolution_mode_enabled": False,
            "evolution_consecutive_failures": 2,
        }

    def load_state(self):
        return dict(self._state)

    def save_state(self, st):
        self._state = dict(st)

    def append_jsonl(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def persist_queue_snapshot(self, reason=""):
        return None

    def sort_pending(self):
        return None

    def send_with_budget(self, chat_id, text):
        return None


def test_task_done_evolution_logs_attempt_result_with_error_signal(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "events.jsonl").write_text(
        json.dumps({"task_id": "evo-1", "type": "tool_error"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    evt = {
        "task_id": "evo-1",
        "task_type": "evolution",
        "worker_id": 1,
        "cost_usd": 0.01,
        "total_rounds": 0,
        "ts": "2026-02-25T14:00:00+00:00",
    }
    _handle_task_done(evt, ctx)

    evolution_logs = _read_jsonl(logs_dir / "evolution_log.jsonl")
    attempt = [row for row in evolution_logs if row.get("type") == "evolution_attempt_result"]
    assert len(attempt) == 1
    assert attempt[0]["status"] == "failure"
    assert attempt[0]["error_signal"] == "tool_error"
    assert attempt[0]["reason"] == "runtime_error"


def test_toggle_evolution_on_resets_breaker_and_logs_context(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    _handle_toggle_evolution({"enabled": True}, ctx)

    assert ctx._state["evolution_mode_enabled"] is True
    assert ctx._state["evolution_consecutive_failures"] == 0

    logs = _read_jsonl(tmp_path / "logs" / "evolution_log.jsonl")
    reset_events = [row for row in logs if row.get("type") == "evolution_breaker_reset"]
    assert len(reset_events) == 1
    assert reset_events[0]["source"] == "toggle_evolution_tool"
    assert reset_events[0]["previous_failures"] == 2
