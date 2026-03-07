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
