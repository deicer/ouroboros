import json
import pathlib

from ouroboros.tools.registry import ToolRegistry


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_update_identity_writes_journal_and_keeps_response(tmp_path: pathlib.Path):
    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    content = "# Who I Am\n\nI am still Ouroboros."

    result = registry.execute("update_identity", {"content": content})

    assert result == f"OK: identity updated ({len(content)} chars)"
    assert (tmp_path / "memory" / "identity.md").read_text(encoding="utf-8") == content

    journal = _read_jsonl(tmp_path / "memory" / "identity_journal.jsonl")
    assert len(journal) == 1
    entry = journal[0]
    assert isinstance(entry.get("ts"), str) and entry["ts"]
    assert entry["content_len"] == len(content)
    assert entry["preview"] == content[:500]


def test_update_user_context_writes_journal_and_keeps_responses(tmp_path: pathlib.Path):
    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    short_content = "User likes concise answers."

    short_result = registry.execute("update_user_context", {"content": short_content})

    assert short_result == f"OK: user context updated ({len(short_content)} chars)"
    assert (tmp_path / "memory" / "USER_CONTEXT.md").read_text(encoding="utf-8") == short_content

    long_content = "x" * 1001
    long_result = registry.execute("update_user_context", {"content": long_content})
    assert (
        long_result
        == f"OK: user context updated ({len(long_content)} chars)"
        f" WARNING: content is {len(long_content)} chars, Bible section 5 says keep under 1000."
    )

    journal = _read_jsonl(tmp_path / "memory" / "user_context_journal.jsonl")
    assert len(journal) == 2
    assert journal[0]["content_len"] == len(short_content)
    assert journal[0]["preview"] == short_content[:500]
    assert journal[1]["content_len"] == len(long_content)
    assert journal[1]["preview"] == long_content[:500]
