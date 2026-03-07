from __future__ import annotations

from ouroboros.consciousness import _clamp_wakeup_seconds, _default_wakeup_seconds


def test_default_wakeup_is_short_in_local_llm_mode(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://host.docker.internal:3455/v1")
    assert _default_wakeup_seconds() == 10


def test_default_wakeup_stays_300_for_openrouter(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert _default_wakeup_seconds() == 300


def test_clamp_wakeup_allows_10_seconds_in_local_mode(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:3455/v1")
    assert _clamp_wakeup_seconds(3) == 10
    assert _clamp_wakeup_seconds(10) == 10


def test_clamp_wakeup_keeps_openrouter_minimum(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert _clamp_wakeup_seconds(10) == 60
