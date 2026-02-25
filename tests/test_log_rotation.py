from __future__ import annotations

from supervisor.state import rotate_chat_log_if_needed, rotate_logs_if_needed


def test_rotate_chat_log_if_needed_archives_and_truncates(tmp_path):
    drive_root = tmp_path
    logs_dir = drive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    chat = logs_dir / "chat.jsonl"
    original = '{"msg":"hello"}\n' * 4
    chat.write_text(original, encoding="utf-8")

    rotate_chat_log_if_needed(drive_root, max_bytes=10)

    assert chat.read_text(encoding="utf-8") == ""
    archived = list((drive_root / "archive").glob("chat_*.jsonl"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == original


def test_rotate_logs_if_needed_rotates_large_logs_and_honors_env(monkeypatch, tmp_path):
    drive_root = tmp_path
    logs_dir = drive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    payload = '{"k":"v"}\n' * 4
    for name in [
        "events.jsonl",
        "tools.jsonl",
        "thinking_trace.jsonl",
        "progress.jsonl",
        "supervisor.jsonl",
        "chat.jsonl",
    ]:
        (logs_dir / name).write_text(payload, encoding="utf-8")

    monkeypatch.setenv("OUROBOROS_LOG_ROTATE_MAX_BYTES", "1")
    monkeypatch.setenv("OUROBOROS_LOG_ROTATE_MAX_BYTES_CHAT", "10_000")

    rotate_logs_if_needed(drive_root, force=True)

    for name in [
        "events.jsonl",
        "tools.jsonl",
        "thinking_trace.jsonl",
        "progress.jsonl",
        "supervisor.jsonl",
    ]:
        log_path = logs_dir / name
        stem = name.removesuffix(".jsonl")
        assert log_path.read_text(encoding="utf-8") == ""
        archived = list((drive_root / "archive").glob(f"{stem}_*.jsonl"))
        assert len(archived) == 1
        assert archived[0].read_text(encoding="utf-8") == payload

    # chat has a larger per-log threshold, so it should not rotate here
    assert (logs_dir / "chat.jsonl").read_text(encoding="utf-8") == payload
    assert not list((drive_root / "archive").glob("chat_*.jsonl"))
