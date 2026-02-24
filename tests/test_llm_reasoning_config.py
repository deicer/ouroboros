import os

from ouroboros.llm import build_reasoning_config


def test_grok_reasoning_enabled_by_default(monkeypatch):
    monkeypatch.delenv("OUROBOROS_REASONING_ENABLED", raising=False)
    cfg = build_reasoning_config("x-ai/grok-4.1-fast", reasoning_effort="medium")
    assert cfg == {"enabled": True, "exclude": True}


def test_grok_reasoning_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("OUROBOROS_REASONING_ENABLED", "false")
    cfg = build_reasoning_config("x-ai/grok-4.1-fast", reasoning_effort="high")
    assert cfg == {"enabled": False, "exclude": True}


def test_grok_reasoning_disabled_when_effort_none(monkeypatch):
    monkeypatch.setenv("OUROBOROS_REASONING_ENABLED", "true")
    cfg = build_reasoning_config("x-ai/grok-4.1-fast", reasoning_effort="none")
    assert cfg == {"enabled": False, "exclude": True}


def test_non_grok_models_keep_effort(monkeypatch):
    monkeypatch.setenv("OUROBOROS_REASONING_ENABLED", "false")
    cfg = build_reasoning_config("anthropic/claude-sonnet-4.6", reasoning_effort="high")
    assert cfg == {"effort": "high", "exclude": True}

