import pathlib
import tempfile

from ouroboros.tools.core import _drive_read, _drive_write, _repo_read, _summarize_dialogue
from ouroboros.tools.registry import ToolContext
from ouroboros.llm import build_response_session_id


def _mk_ctx(repo_dir: pathlib.Path, drive_root: pathlib.Path) -> ToolContext:
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def test_drive_read_accepts_absolute_data_path():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "memory").mkdir(parents=True, exist_ok=True)
        fp = drive / "memory" / "scratchpad.md"
        fp.write_text("hello-drive", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _drive_read(ctx, str(fp))
        assert out == "hello-drive"


def test_repo_read_ignores_limit_and_returns_full_content():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "AGENT.md"
        fp.write_text("abcdef", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, str(fp), limit=3)
        assert out == "abcdef"


def test_repo_read_maps_legacy_app_tools_path_to_package_layout():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros" / "tools").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "tools" / "sample.py"
        fp.write_text("print('ok')", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "/app/tools/sample.py")
        assert "print('ok')" in out


def test_repo_read_directory_returns_helpful_message():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "docs").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, str(repo / "docs"))
        assert "expects a file" in out
        assert "repo_list" in out


def test_repo_read_relative_tools_path_maps_to_ouroboros_package():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros" / "tools").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "tools" / "whisper.py"
        fp.write_text("# whisper tool", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "tools/whisper.py")
        assert "# whisper tool" in out


def test_repo_read_relative_loop_path_maps_to_ouroboros_package():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "loop.py"
        fp.write_text("print('loop')", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "loop.py")
        assert "print('loop')" in out


def test_drive_write_absolute_data_path_writes_under_drive_root():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)

        ctx = _mk_ctx(repo, drive)
        target = drive / "memory" / "goals.json"
        out = _drive_write(ctx, "/data/memory/goals.json", "[]", mode="overwrite")

        assert out.startswith("OK: wrote overwrite")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "[]"
        assert not (drive / "data" / "memory" / "goals.json").exists()


def test_drive_read_alias_identity_maps_to_memory():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "memory").mkdir(parents=True, exist_ok=True)
        fp = drive / "memory" / "identity.md"
        fp.write_text("who-am-i", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _drive_read(ctx, "identity.md")
        assert out == "who-am-i"


def test_drive_read_legacy_drive_root_prefix_maps_correctly():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "logs").mkdir(parents=True, exist_ok=True)
        fp = drive / "logs" / "progress.jsonl"
        fp.write_text("{\"ok\":true}\n", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _drive_read(ctx, "drive_root/logs/progress.jsonl")
        assert "{\"ok\":true}" in out


def test_repo_read_identity_redirects_to_drive_memory_file():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "memory").mkdir(parents=True, exist_ok=True)
        fp = drive / "memory" / "identity.md"
        fp.write_text("identity-from-drive", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "identity.md")
        assert out == "identity-from-drive"


def test_summarize_dialogue_uses_prompt_cache_key_and_logs_model(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    drive = tmp_path / "data"
    repo.mkdir(parents=True, exist_ok=True)
    (drive / "logs").mkdir(parents=True, exist_ok=True)
    (drive / "memory").mkdir(parents=True, exist_ok=True)
    (drive / "state").mkdir(parents=True, exist_ok=True)
    (drive / "logs" / "chat.jsonl").write_text(
        '{"ts":"2026-03-15T00:00:00Z","direction":"in","text":"hello"}\n',
        encoding="utf-8",
    )
    (drive / "state" / "state.json").write_text('{"session_id":"sess-sum"}', encoding="utf-8")

    captured = {}

    class DummyLLM:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return {"content": "Summary body"}, {"prompt_tokens": 200, "completion_tokens": 40, "cached_tokens": 80, "cost": 0.02}

    monkeypatch.setattr("ouroboros.llm.LLMClient", lambda: DummyLLM())
    monkeypatch.setattr("ouroboros.llm.get_light_model_from_env", lambda: "gpt-5.4")

    ctx = ToolContext(repo_dir=repo, drive_root=drive, task_id="task-sum")
    out = _summarize_dialogue(ctx, last_n=10)

    expected_key = build_response_session_id(
        scope="summarize_dialogue",
        runtime_session_id="sess-sum",
        task_id="task-sum",
    )
    assert "Summary body" in out
    assert captured["prompt_cache_key"] == expected_key
    assert captured["session_id"] == expected_key
    usage_evt = ctx.pending_events[-1]
    assert usage_evt["model"] == "gpt-5.4"
    assert usage_evt["prompt_tokens"] == 200
    assert usage_evt["cached_tokens"] == 80
