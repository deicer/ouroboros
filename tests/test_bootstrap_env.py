from pathlib import Path

from ouroboros.bootstrap_env import should_use_openrouter_budget_from_env


def test_bootstrap_env_detects_local_base_url(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:3455/v1")
    assert should_use_openrouter_budget_from_env() is False


def test_bootstrap_env_defaults_to_openrouter_when_unset(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert should_use_openrouter_budget_from_env() is True


def test_launcher_no_longer_imports_llm_budget_helper():
    launcher_src = Path("/home/deicer/ouroboros/launcher.py").read_text(encoding="utf-8")
    assert "from ouroboros.llm import should_use_openrouter_budget" not in launcher_src
    assert "from ouroboros.bootstrap_env import should_use_openrouter_budget_from_env" in launcher_src


def test_state_no_longer_imports_llm_budget_helper():
    state_src = Path("/home/deicer/ouroboros/supervisor/state.py").read_text(encoding="utf-8")
    assert "from ouroboros.llm import should_use_openrouter_budget" not in state_src
    assert "from ouroboros.bootstrap_env import should_use_openrouter_budget_from_env" in state_src
