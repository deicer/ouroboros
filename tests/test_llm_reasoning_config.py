import os

from ouroboros.llm import (
    build_reasoning_config,
    get_fallback_models_from_env,
    get_free_models_from_env,
    get_paid_models_from_env,
    is_free_model,
)


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


def test_is_free_model_heuristics():
    assert is_free_model("arcee-ai/trinity-large-preview:free") is True
    assert is_free_model("opencode/minimax-m2.5-free") is True
    assert is_free_model("x-ai/grok-4.1-fast") is False


def test_get_free_models_from_env_explicit_list(monkeypatch):
    monkeypatch.setenv(
        "OUROBOROS_MODEL_FREE_LIST",
        "arcee-ai/trinity-large-preview:free,z-ai/glm-4.5-air:free",
    )
    monkeypatch.setenv("OUROBOROS_MODEL", "x-ai/grok-4.1-fast")
    models = get_free_models_from_env(active_model="x-ai/grok-4.1-fast")
    assert models == [
        "arcee-ai/trinity-large-preview:free",
        "z-ai/glm-4.5-air:free",
    ]


def test_get_free_models_from_env_from_fallback(monkeypatch):
    monkeypatch.delenv("OUROBOROS_MODEL_FREE_LIST", raising=False)
    monkeypatch.setenv("OUROBOROS_MODEL", "x-ai/grok-4.1-fast")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "x-ai/grok-4.1-fast")
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "x-ai/grok-4.1-fast")
    monkeypatch.setenv(
        "OUROBOROS_MODEL_FALLBACK_LIST",
        "arcee-ai/trinity-large-preview:free,z-ai/glm-4.5-air:free",
    )
    models = get_free_models_from_env(active_model="x-ai/grok-4.1-fast")
    assert models == [
        "arcee-ai/trinity-large-preview:free",
        "z-ai/glm-4.5-air:free",
    ]


def test_get_paid_models_from_env_explicit_list(monkeypatch):
    monkeypatch.setenv(
        "OUROBOROS_MODEL_PAID_LIST",
        "x-ai/grok-4.1-fast,anthropic/claude-sonnet-4.6,google/gemini-3-pro-preview",
    )
    monkeypatch.setenv("OUROBOROS_MODEL", "x-ai/grok-4.1-fast")
    models = get_paid_models_from_env(active_model="x-ai/grok-4.1-fast")
    assert models == [
        "anthropic/claude-sonnet-4.6",
        "google/gemini-3-pro-preview",
    ]


def test_get_fallback_models_respects_paid_then_free_priority(monkeypatch):
    monkeypatch.setenv(
        "OUROBOROS_MODEL_PAID_LIST",
        "x-ai/grok-4.1-fast,anthropic/claude-sonnet-4.6",
    )
    monkeypatch.setenv(
        "OUROBOROS_MODEL_FREE_LIST",
        "arcee-ai/trinity-large-preview:free,z-ai/glm-4.5-air:free",
    )
    candidates = get_fallback_models_from_env(active_model="x-ai/grok-4.1-fast")
    assert candidates == [
        "anthropic/claude-sonnet-4.6",
        "arcee-ai/trinity-large-preview:free",
        "z-ai/glm-4.5-air:free",
    ]
