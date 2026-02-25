import json
import pathlib
from types import SimpleNamespace

from supervisor.events import _handle_task_done


class _DummyCtx:
    def __init__(self, root: pathlib.Path):
        self.DRIVE_ROOT = root
        self.REPO_DIR = root
        self.RUNNING = {"rev-1": {"started": 1}}
        self.WORKERS = {1: SimpleNamespace(busy_task_id="rev-1")}
        self._state = {"evolution_consecutive_failures": 0}
        self.snapshot_reasons: list[str] = []

    def load_state(self):
        return dict(self._state)

    def save_state(self, st):
        self._state = dict(st)

    def append_jsonl(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def persist_queue_snapshot(self, reason=""):
        self.snapshot_reasons.append(reason)


def _append_tool_log(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_task_done_autonomous_commit_appends_architecture_md_and_deduplicates(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "task_results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "task_results" / "rev-1.json").write_text(
        json.dumps(
            {
                "task_id": "rev-1",
                "status": "completed",
                "result": "Refactored retry path in supervisor/telegram.py",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "ARCHITECTURE.md").write_text(
        "# Архитектура Ouroboros\n\n"
        "## 12. Журнал архитектурных изменений (авто, append-only)\n",
        encoding="utf-8",
    )

    _append_tool_log(
        tmp_path / "logs" / "tools.jsonl",
        {
            "ts": "2026-02-25T12:00:00+00:00",
            "tool": "repo_commit_push",
            "task_id": "rev-1",
            "args": {"commit_message": "feat(telegram): add retry backoff"},
            "result_preview": "OK: committed and pushed to ouroboros: feat(telegram): add retry backoff",
        },
    )

    evt = {
        "task_id": "rev-1",
        "task_type": "review",
        "worker_id": 1,
        "cost_usd": 0.35,
        "total_rounds": 3,
        "ts": "2026-02-25T12:01:00+00:00",
    }
    _handle_task_done(evt, ctx)
    _handle_task_done(evt, ctx)

    text = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "architecture-task:rev-1" in text
    assert text.count("architecture-task:rev-1") == 1
    assert "feat(telegram): add retry backoff" in text
    assert "Refactored retry path in supervisor/telegram.py" in text


def test_task_done_without_successful_commit_does_not_append_architecture_md(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ARCHITECTURE.md").write_text(
        "# Архитектура Ouroboros\n\n"
        "## 12. Журнал архитектурных изменений (авто, append-only)\n",
        encoding="utf-8",
    )
    _append_tool_log(
        tmp_path / "logs" / "tools.jsonl",
        {
            "ts": "2026-02-25T12:02:00+00:00",
            "tool": "repo_commit_push",
            "task_id": "rev-1",
            "args": {"commit_message": "chore: no-op"},
            "result_preview": "⚠️ GIT_NO_CHANGES: nothing to commit.",
        },
    )

    evt = {
        "task_id": "rev-1",
        "task_type": "review",
        "worker_id": 1,
        "cost_usd": 0.02,
        "total_rounds": 1,
        "ts": "2026-02-25T12:03:00+00:00",
    }
    _handle_task_done(evt, ctx)

    text = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "architecture-task:rev-1" not in text


def test_user_task_with_commit_does_not_append_architecture_md(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ARCHITECTURE.md").write_text(
        "# Архитектура Ouroboros\n\n"
        "## 12. Журнал архитектурных изменений (авто, append-only)\n",
        encoding="utf-8",
    )
    _append_tool_log(
        tmp_path / "logs" / "tools.jsonl",
        {
            "ts": "2026-02-25T12:04:00+00:00",
            "tool": "repo_commit_push",
            "task_id": "rev-1",
            "args": {"commit_message": "feat: user requested change"},
            "result_preview": "OK: committed and pushed to ouroboros: feat: user requested change",
        },
    )
    evt = {
        "task_id": "rev-1",
        "task_type": "task",
        "worker_id": 1,
        "cost_usd": 0.3,
        "total_rounds": 2,
        "ts": "2026-02-25T12:05:00+00:00",
    }
    _handle_task_done(evt, ctx)
    text = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "architecture-task:rev-1" not in text
