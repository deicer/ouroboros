import json
import pathlib

from ouroboros.memory import Memory


def _write_chat(path: pathlib.Path, count: int) -> None:
    lines = []
    for i in range(count):
        lines.append(json.dumps({
            "ts": f"2026-02-25T10:{i:02d}:00+00:00",
            "direction": "in" if i % 2 == 0 else "out",
            "chat_id": 1,
            "user_id": 1,
            "text": f"msg-{i}",
        }, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_summarize_old_history_archives_and_keeps_tail(tmp_path):
    mem = Memory(drive_root=tmp_path)
    chat_path = mem.logs_path("chat.jsonl")
    _write_chat(chat_path, 7)

    result = mem.summarize_old_history(keep_last_n=3)
    assert "Compacted 4 old messages" in result

    active_lines = [x for x in chat_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(active_lines) == 3
    assert "msg-4" in active_lines[0]
    assert "msg-6" in active_lines[-1]

    archive_path = mem.chat_archive_path()
    archive_lines = [x for x in archive_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(archive_lines) == 4
    assert "msg-0" in archive_lines[0]
    assert "msg-3" in archive_lines[-1]

    summary_text = mem.chat_history_summary_path().read_text(encoding="utf-8")
    assert "chat compaction" in summary_text
    assert "Compacted messages: 4" in summary_text
    dialogue_summary_text = mem.dialogue_summary_path().read_text(encoding="utf-8")
    assert "chat compaction" in dialogue_summary_text
    assert "Compacted messages: 4" in dialogue_summary_text


def test_summarize_old_history_noop_when_small(tmp_path):
    mem = Memory(drive_root=tmp_path)
    chat_path = mem.logs_path("chat.jsonl")
    _write_chat(chat_path, 2)

    result = mem.summarize_old_history(keep_last_n=5)
    assert "No compaction needed" in result
    assert not mem.chat_archive_path().exists()
    assert not mem.chat_history_summary_path().exists()
    assert not mem.dialogue_summary_path().exists()


def test_chat_history_auto_compacts_when_too_large(monkeypatch, tmp_path):
    mem = Memory(drive_root=tmp_path)
    chat_path = mem.logs_path("chat.jsonl")
    _write_chat(chat_path, 6)

    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_AUTO_SUMMARIZE", "true")
    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_MAX_BYTES", "10")
    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_KEEP_LAST_N", "2")

    text = mem.chat_history(count=10)
    assert "Showing 2 messages" in text

    active_lines = [x for x in chat_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(active_lines) == 2
    assert mem.chat_archive_path().exists()
    assert mem.chat_history_summary_path().exists()
    assert mem.dialogue_summary_path().exists()


def test_context_compaction_uses_message_count_cap(monkeypatch, tmp_path):
    mem = Memory(drive_root=tmp_path)
    chat_path = mem.logs_path("chat.jsonl")
    _write_chat(chat_path, 9)

    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_AUTO_SUMMARIZE", "true")
    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_MAX_ACTIVE_MESSAGES", "4")
    monkeypatch.setenv("OUROBOROS_CHAT_HISTORY_KEEP_LAST_N", "3")

    mem.ensure_chat_history_compacted_for_context()

    active_lines = [x for x in chat_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(active_lines) == 3
    assert "msg-6" in active_lines[0]
    assert "msg-8" in active_lines[-1]
    assert mem.chat_archive_path().exists()
    assert mem.dialogue_summary_path().exists()
