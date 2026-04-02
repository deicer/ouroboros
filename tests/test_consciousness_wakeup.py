from __future__ import annotations

import json
import queue

from ouroboros.consciousness import (
    BackgroundConsciousness,
    _clamp_wakeup_seconds,
    _default_wakeup_seconds,
)
from ouroboros.llm import build_response_session_id


def test_default_wakeup_is_short_in_local_llm_mode(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://host.docker.internal:3455/v1")
    assert _default_wakeup_seconds() == 120


def test_default_wakeup_stays_300_for_openrouter(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert _default_wakeup_seconds() == 300


def test_clamp_wakeup_allows_120_seconds_in_local_mode(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:3455/v1")
    assert _clamp_wakeup_seconds(3) == 120
    assert _clamp_wakeup_seconds(120) == 120


def test_clamp_wakeup_keeps_openrouter_minimum(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert _clamp_wakeup_seconds(10) == 60


def test_background_start_is_hard_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OUROBOROS_BG_ENABLED", "false")
    drive_root = tmp_path / "drive"
    repo_dir = tmp_path / "repo"
    (drive_root / "logs").mkdir(parents=True)
    repo_dir.mkdir(parents=True)

    bg = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=None,
        owner_chat_id_fn=lambda: None,
    )

    result = bg.start()

    assert "disabled" in result.lower()
    assert bg.is_running is False


def test_background_context_includes_idle_self_improvement_policy(tmp_path):
    drive_root = tmp_path / "drive"
    repo_dir = tmp_path / "repo"
    (drive_root / "logs").mkdir(parents=True)
    (repo_dir / "prompts").mkdir(parents=True)
    (repo_dir / "prompts" / "CONSCIOUSNESS.md").write_text("Base prompt.", encoding="utf-8")

    bg = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=None,
        owner_chat_id_fn=lambda: None,
    )

    stable_context, dynamic_context = bg._build_context_blocks()

    assert "Owner tasks outrank background work." in stable_context
    assert "If there is no active owner task" in stable_context
    assert "Do not reread the same file path" in stable_context
    assert "UTC:" not in stable_context
    assert "UTC:" in dynamic_context


def test_background_context_keeps_recent_trace_and_runtime_in_dynamic_block(tmp_path):
    drive_root = tmp_path / "drive"
    repo_dir = tmp_path / "repo"
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (repo_dir / "prompts").mkdir(parents=True)
    (repo_dir / "prompts" / "CONSCIOUSNESS.md").write_text("Base prompt.", encoding="utf-8")
    (drive_root / "memory" / "identity.md").write_text("identity", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("scratch", encoding="utf-8")
    (drive_root / "memory" / "dialogue_summary.md").write_text("summary", encoding="utf-8")

    bg = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=None,
        owner_chat_id_fn=lambda: None,
    )
    bg.inject_observation("obs-1")

    stable_context, dynamic_context = bg._build_context_blocks()

    assert "## Identity" in stable_context
    assert "## Scratchpad" in stable_context
    assert "## Dialogue Summary" in stable_context
    assert "## Runtime" not in stable_context
    assert "## Runtime" in dynamic_context
    assert "## Recent observations" in dynamic_context


def test_background_think_uses_prompt_cache_key_and_emits_modeled_usage(monkeypatch, tmp_path):
    drive_root = tmp_path / "drive"
    repo_dir = tmp_path / "repo"
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "state").mkdir(parents=True)
    (repo_dir / "prompts").mkdir(parents=True)
    (repo_dir / "prompts" / "CONSCIOUSNESS.md").write_text("Base prompt.", encoding="utf-8")
    (drive_root / "state" / "state.json").write_text(
        json.dumps({"session_id": "sess-bg"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.delenv("OUROBOROS_MODEL_FREE_LIST", raising=False)
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "gpt-5.4")

    q = queue.Queue()
    bg = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=q,
        owner_chat_id_fn=lambda: None,
    )

    monkeypatch.setattr(bg, "_maybe_schedule_arch_review", lambda: None)
    monkeypatch.setattr(bg, "_build_context_blocks", lambda: ("stable", "dynamic"))
    monkeypatch.setattr(bg, "_tool_schemas", lambda: [])
    monkeypatch.setattr(bg, "_append_thinking_trace", lambda *a, **k: None)

    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return (
            {"content": "done", "tool_calls": []},
            {"prompt_tokens": 1200, "completion_tokens": 50, "cached_tokens": 640, "cost": 0.12},
        )

    monkeypatch.setattr(bg._llm, "chat", fake_chat)

    bg._think()

    expected_session_id = build_response_session_id(
        scope="consciousness",
        runtime_session_id="sess-bg",
    )
    assert captured["session_id"] == expected_session_id
    assert captured["prompt_cache_key"] == expected_session_id
    assert captured["messages"] == [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "Wake up. Think."},
        {"role": "user", "content": "dynamic"},
    ]

    usage_evt = q.get_nowait()
    assert usage_evt["type"] == "llm_usage"
    assert usage_evt["model"] == "gpt-5.4"
    assert usage_evt["round"] == 1
    assert usage_evt["prompt_tokens"] == 1200
    assert usage_evt["cached_tokens"] == 640
    assert usage_evt["category"] == "consciousness"
