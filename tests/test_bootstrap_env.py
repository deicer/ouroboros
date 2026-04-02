from pathlib import Path

from ouroboros.bootstrap_env import (
    should_autostart_background_from_env,
    should_deliver_progress_to_owner_from_env,
    should_deliver_proactive_owner_messages_from_env,
    should_notify_long_running_tasks_to_owner_from_env,
    should_notify_scheduled_tasks_to_owner_from_env,
    should_use_openrouter_budget_from_env,
)


def test_bootstrap_env_detects_local_base_url(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:3455/v1")
    assert should_use_openrouter_budget_from_env() is False


def test_bootstrap_env_defaults_to_openrouter_when_unset(monkeypatch):
    monkeypatch.delenv("OUROBOROS_LLM_BASE_URL", raising=False)
    assert should_use_openrouter_budget_from_env() is True


def test_background_autostart_defaults_to_on(monkeypatch):
    monkeypatch.delenv("OUROBOROS_BG_ENABLED", raising=False)
    assert should_autostart_background_from_env() is True


def test_background_autostart_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OUROBOROS_BG_ENABLED", "false")
    assert should_autostart_background_from_env() is False


def test_owner_notification_policies_default_to_quiet(monkeypatch):
    monkeypatch.delenv("OUROBOROS_SEND_PROGRESS_TO_OWNER", raising=False)
    monkeypatch.delenv("OUROBOROS_SEND_PROACTIVE_MESSAGES_TO_OWNER", raising=False)
    monkeypatch.delenv("OUROBOROS_SEND_LONG_TASK_HEARTBEATS", raising=False)
    monkeypatch.delenv("OUROBOROS_SEND_SCHEDULED_TASK_NOTIFICATIONS", raising=False)
    assert should_deliver_progress_to_owner_from_env() is False
    assert should_deliver_proactive_owner_messages_from_env() is False
    assert should_notify_long_running_tasks_to_owner_from_env() is False
    assert should_notify_scheduled_tasks_to_owner_from_env() is False


def test_owner_notification_policies_can_be_enabled(monkeypatch):
    monkeypatch.setenv("OUROBOROS_SEND_PROGRESS_TO_OWNER", "true")
    monkeypatch.setenv("OUROBOROS_SEND_PROACTIVE_MESSAGES_TO_OWNER", "1")
    monkeypatch.setenv("OUROBOROS_SEND_LONG_TASK_HEARTBEATS", "yes")
    monkeypatch.setenv("OUROBOROS_SEND_SCHEDULED_TASK_NOTIFICATIONS", "on")
    assert should_deliver_progress_to_owner_from_env() is True
    assert should_deliver_proactive_owner_messages_from_env() is True
    assert should_notify_long_running_tasks_to_owner_from_env() is True
    assert should_notify_scheduled_tasks_to_owner_from_env() is True


def test_launcher_no_longer_imports_llm_budget_helper():
    launcher_src = Path("/home/deicer/ouroboros/launcher.py").read_text(encoding="utf-8")
    assert "from ouroboros.llm import should_use_openrouter_budget" not in launcher_src
    assert "from ouroboros.bootstrap_env import" in launcher_src
    assert "should_use_openrouter_budget_from_env" in launcher_src


def test_state_no_longer_imports_llm_budget_helper():
    state_src = Path("/home/deicer/ouroboros/supervisor/state.py").read_text(encoding="utf-8")
    assert "from ouroboros.llm import should_use_openrouter_budget" not in state_src
    assert "from ouroboros.bootstrap_env import should_use_openrouter_budget_from_env" in state_src


def test_launcher_has_background_autostart_guard():
    launcher_src = Path("/home/deicer/ouroboros/launcher.py").read_text(encoding="utf-8")
    assert "should_autostart_background_from_env" in launcher_src
    assert 'os.environ.get("OUROBOROS_BG_ENABLED"' not in launcher_src


def test_status_command_no_longer_falls_through_to_llm():
    launcher_src = Path("/home/deicer/ouroboros/launcher.py").read_text(encoding="utf-8")
    assert 'return "[Supervisor handled /status' not in launcher_src
    assert 'if lowered.startswith("/status"):' in launcher_src
    assert "send_with_budget(chat_id, status, force_budget=True)" in launcher_src
    assert "return True" in launcher_src


def test_env_example_documents_openrouter_free_mode():
    env_example = Path("/home/deicer/ouroboros/.env.example").read_text(encoding="utf-8")
    assert "OUROBOROS_MODEL=openrouter/free" in env_example
    assert "OUROBOROS_MODEL_CODE=openrouter/free" in env_example
    assert "OUROBOROS_MODEL_LIGHT=openrouter/free" in env_example
    assert "OUROBOROS_MODEL_FREE_LIST=openrouter/free" in env_example
    assert "OUROBOROS_SELF_EDIT_ONLY=true" in env_example


def test_readme_documents_self_edit_only_default_and_free_router():
    readme = Path("/home/deicer/ouroboros/README.md").read_text(encoding="utf-8")
    assert "| `OUROBOROS_MODEL` | `openrouter/free` |" in readme
    assert "| `OUROBOROS_MODEL_CODE` | `openrouter/free` |" in readme
    assert "| `OUROBOROS_MODEL_LIGHT` | `openrouter/free` |" in readme
    assert "| `OUROBOROS_MODEL_FREE_LIST` | `openrouter/free` |" in readme
    assert "| `OUROBOROS_SELF_EDIT_ONLY` | `true` |" in readme
