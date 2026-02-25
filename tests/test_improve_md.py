import json
import pathlib
from types import SimpleNamespace

from supervisor.events import _handle_task_done


class _DummyCtx:
    def __init__(self, root: pathlib.Path):
        self.DRIVE_ROOT = root
        self.REPO_DIR = root
        self.RUNNING = {"evo-1": {"started": 1}}
        self.WORKERS = {1: SimpleNamespace(busy_task_id="evo-1")}
        self._state = {"evolution_consecutive_failures": 2}
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


def test_task_done_evolution_success_appends_improve_md_and_deduplicates(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "task_results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "task_results" / "evo-1.json").write_text(
        json.dumps(
            {
                "task_id": "evo-1",
                "status": "completed",
                "result": "Added retry guard for OpenCode timeout loop",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "IMPROVE.md").write_text(
        "# How to Improve Effectively\n\nThis file captures lessons.\n",
        encoding="utf-8",
    )

    evt = {
        "task_id": "evo-1",
        "task_type": "evolution",
        "worker_id": 1,
        "cost_usd": 0.25,
        "total_rounds": 2,
        "ts": "2026-02-25T12:00:00+00:00",
    }
    _handle_task_done(evt, ctx)
    _handle_task_done(evt, ctx)

    text = (tmp_path / "IMPROVE.md").read_text(encoding="utf-8")
    assert "evolution-task:evo-1" in text
    assert text.count("evolution-task:evo-1") == 1
    assert "Сработало" in text
    assert "Не сработало" in text
    assert "Added retry guard for OpenCode timeout loop" in text


def test_task_done_evolution_unsuccessful_does_not_append_improve_md(tmp_path: pathlib.Path):
    ctx = _DummyCtx(tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "IMPROVE.md").write_text("# How to Improve Effectively\n", encoding="utf-8")

    evt = {
        "task_id": "evo-2",
        "task_type": "evolution",
        "worker_id": 1,
        "cost_usd": 0.01,
        "total_rounds": 0,
        "ts": "2026-02-25T12:01:00+00:00",
    }
    _handle_task_done(evt, ctx)

    text = (tmp_path / "IMPROVE.md").read_text(encoding="utf-8")
    assert "evolution-task:evo-2" not in text
