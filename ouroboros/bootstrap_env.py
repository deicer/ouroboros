"""Bootstrap-safe env helpers.

Keep this module dependency-light so launcher can read config before safe_restart()
without preloading mutable runtime modules like ouroboros.llm.
"""

from __future__ import annotations

import os

DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"


def should_use_openrouter_budget_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_LLM_BASE_URL", "") or "").strip()
    if raw:
        return raw.rstrip("/") == DEFAULT_LLM_BASE_URL
    return True


def should_autostart_background_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_BG_ENABLED", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def should_bootstrap_git_reset_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_BOOTSTRAP_GIT_RESET", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def should_deliver_progress_to_owner_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_SEND_PROGRESS_TO_OWNER", "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_deliver_proactive_owner_messages_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_SEND_PROACTIVE_MESSAGES_TO_OWNER", "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_notify_long_running_tasks_to_owner_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_SEND_LONG_TASK_HEARTBEATS", "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_notify_scheduled_tasks_to_owner_from_env() -> bool:
    raw = str(os.environ.get("OUROBOROS_SEND_SCHEDULED_TASK_NOTIFICATIONS", "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
