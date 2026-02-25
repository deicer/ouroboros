import json
import pathlib
import tempfile

from ouroboros.memory import Memory


def test_read_jsonl_tail_does_not_call_read_text(monkeypatch):
    """Tail reader should avoid full-file read_text() for large logs."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        logs_dir = root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "chat.jsonl"
        log_path.write_text(
            "\n".join(json.dumps({"idx": i}) for i in range(2000)) + "\n",
            encoding="utf-8",
        )

        def _boom(*args, **kwargs):
            raise AssertionError("read_text() should not be used in read_jsonl_tail")

        monkeypatch.setattr(pathlib.Path, "read_text", _boom)
        mem = Memory(drive_root=root)
        entries = mem.read_jsonl_tail("chat.jsonl", max_entries=3)
        assert [e["idx"] for e in entries] == [1997, 1998, 1999]


def test_read_jsonl_tail_ignores_invalid_utf8_and_broken_json_lines():
    """Corrupted tail lines should be skipped without crashing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        logs_dir = root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "events.jsonl"
        with log_path.open("wb") as f:
            for i in range(5):
                f.write((json.dumps({"idx": i}) + "\n").encode("utf-8"))
            f.write(b'{"idx": 5}\n')
            f.write(b"\xff\xfe\xfa\n")
            f.write(b"{not json}\n")
            f.write('{"idx": 6, "text": "хорошо"}\n'.encode("utf-8"))

        mem = Memory(drive_root=root)
        entries = mem.read_jsonl_tail("events.jsonl", max_entries=4)
        assert [e["idx"] for e in entries] == [5, 6]


def test_load_user_context_migrates_lowercase_alias_to_canonical():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        alias_path = root / "memory" / "user_context.md"
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        alias_path.write_text("Контекст из alias-файла", encoding="utf-8")

        mem = Memory(drive_root=root)
        loaded = mem.load_user_context()

        assert loaded == "Контекст из alias-файла"
        assert mem.user_context_path().exists()
        assert mem.user_context_path().read_text(encoding="utf-8") == "Контекст из alias-файла"


def test_load_user_context_prefers_canonical_file_when_both_exist():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        memory_dir = root / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "USER_CONTEXT.md").write_text("Канонический контекст", encoding="utf-8")
        (memory_dir / "user_context.md").write_text("Контекст alias", encoding="utf-8")

        mem = Memory(drive_root=root)
        loaded = mem.load_user_context()

        assert loaded == "Канонический контекст"


def test_default_identity_and_user_context_are_russian():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        mem = Memory(drive_root=root)

        identity = mem.load_identity()
        user_context = mem.load_user_context()

        assert "Я — Ouroboros" in identity
        assert "Контекст пользователя" in user_context
