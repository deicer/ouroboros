import os

from ouroboros.llm import (
    build_reasoning_config,
    get_llm_api_key,
    get_llm_base_url,
    get_fallback_models_from_env,
    get_free_models_from_env,
    get_paid_models_from_env,
    is_free_model,
    should_use_openrouter_budget,
    refresh_model_env_from_dotenv,
    LLMClient,
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


def test_get_fallback_models_respects_free_then_paid_priority(monkeypatch):
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
        "arcee-ai/trinity-large-preview:free",
        "z-ai/glm-4.5-air:free",
        "anthropic/claude-sonnet-4.6",
    ]


def test_refresh_model_env_from_dotenv_runtime(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OUROBOROS_MODEL=anthropic/claude-sonnet-4.6",
                "OUROBOROS_MODEL_PAID_LIST=anthropic/claude-sonnet-4.6,x-ai/grok-4.1-fast",
                "OUROBOROS_MODEL_FREE_LIST=arcee-ai/trinity-large-preview:free",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.delenv("OUROBOROS_MODEL", raising=False)
    monkeypatch.delenv("OUROBOROS_MODEL_PAID_LIST", raising=False)
    monkeypatch.delenv("OUROBOROS_MODEL_FREE_LIST", raising=False)

    changed = refresh_model_env_from_dotenv(force=True)
    assert changed is True
    assert os.environ.get("OUROBOROS_MODEL") == "anthropic/claude-sonnet-4.6"
    assert os.environ.get("OUROBOROS_MODEL_PAID_LIST") == "anthropic/claude-sonnet-4.6,x-ai/grok-4.1-fast"
    assert os.environ.get("OUROBOROS_MODEL_FREE_LIST") == "arcee-ai/trinity-large-preview:free"


def test_local_llm_base_url_and_dummy_key(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:2455/v1")
    monkeypatch.delenv("OUROBOROS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert get_llm_base_url() == "http://127.0.0.1:2455/v1"
    assert get_llm_api_key() == "dummy"
    assert should_use_openrouter_budget() is False


def test_llm_client_defaults_to_env_overrides(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:2455/v1")
    monkeypatch.setenv("OUROBOROS_LLM_API_KEY", "sk-clb-test")

    client = LLMClient()

    assert client._base_url == "http://127.0.0.1:2455/v1"
    assert client._api_key == "sk-clb-test"
