from __future__ import annotations

from pathlib import Path

from ouroboros.tools.control import _send_owner_message
from ouroboros.tools.registry import ToolContext


def test_send_owner_message_is_suppressed_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("OUROBOROS_SEND_PROACTIVE_MESSAGES_TO_OWNER", raising=False)
    ctx = ToolContext(repo_dir=tmp_path, drive_root=tmp_path)
    ctx.current_chat_id = 123

    result = _send_owner_message(ctx, "hello")

    assert "suppressed" in result.lower()
    assert ctx.pending_events == []


def test_send_owner_message_can_be_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OUROBOROS_SEND_PROACTIVE_MESSAGES_TO_OWNER", "true")
    ctx = ToolContext(repo_dir=tmp_path, drive_root=tmp_path)
    ctx.current_chat_id = 123

    result = _send_owner_message(ctx, "hello")

    assert result == "OK: message queued for delivery."
    assert len(ctx.pending_events) == 1
    assert ctx.pending_events[0]["type"] == "send_message"
