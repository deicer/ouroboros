import os
import types

from ouroboros.llm import (
    build_reasoning_config,
    build_prompt_cache_key,
    build_response_session_id,
    fetch_openrouter_pricing,
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


def test_local_llm_reasoning_omits_openrouter_only_exclude(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:2455/v1")
    cfg = build_reasoning_config("gpt-5.4", reasoning_effort="medium")
    assert cfg == {"effort": "medium"}


def test_host_docker_internal_reasoning_omits_openrouter_only_exclude(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://host.docker.internal:3455/v1")
    cfg = build_reasoning_config("gpt-5.1-codex-mini", reasoning_effort="medium")
    assert cfg == {"effort": "medium"}


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


def test_host_docker_internal_uses_dummy_key_in_local_mode(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://host.docker.internal:3455/v1")
    monkeypatch.delenv("OUROBOROS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert get_llm_api_key() == "dummy"
    assert should_use_openrouter_budget() is False


def test_llm_client_defaults_to_env_overrides(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:2455/v1")
    monkeypatch.setenv("OUROBOROS_LLM_API_KEY", "sk-clb-test")

    client = LLMClient()

    assert client._base_url == "http://127.0.0.1:2455/v1"
    assert client._api_key == "sk-clb-test"


def test_fetch_openrouter_pricing_sends_auth_header(monkeypatch):
    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://31.56.196.40:3455/v1")
    monkeypatch.setenv("OUROBOROS_LLM_API_KEY", "sk-clb-test")

    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    def fake_get(url, timeout=0, headers=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["headers"] = headers or {}
        return DummyResponse()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    assert fetch_openrouter_pricing() == {}
    assert captured["url"] == "http://31.56.196.40:3455/v1/models"
    assert captured["timeout"] == 15
    assert captured["headers"]["Authorization"] == "Bearer sk-clb-test"


def test_build_prompt_cache_key_is_stable_for_same_scope():
    key1 = build_prompt_cache_key(
        scope="task_loop",
        model="gpt-5.4",
        task_id="task-1",
        session_id="sess-1",
        tool_names=["repo_read", "run_shell"],
    )
    key2 = build_prompt_cache_key(
        scope="task_loop",
        model="gpt-5.4",
        task_id="task-1",
        session_id="sess-1",
        tool_names=["repo_read", "run_shell"],
    )
    key3 = build_prompt_cache_key(
        scope="task_loop",
        model="gpt-5.4",
        task_id="task-1",
        session_id="sess-1",
        tool_names=["repo_read", "patch_edit"],
    )

    assert key1 == key2
    assert key1 != key3


def test_build_response_session_id_is_stable_per_scope_and_subject():
    sid1 = build_response_session_id(scope="task_loop", runtime_session_id="runtime-1", task_id="task-1")
    sid2 = build_response_session_id(scope="task_loop", runtime_session_id="runtime-1", task_id="task-1")
    sid3 = build_response_session_id(scope="task_loop", runtime_session_id="runtime-1", task_id="task-2")
    sid4 = build_response_session_id(scope="consciousness", runtime_session_id="runtime-1")

    assert sid1 == sid2
    assert sid1 != sid3
    assert sid1 != sid4


def test_llm_chat_sets_prompt_cache_hints_for_gpt_5_4(monkeypatch):
    captured = {}
    monkeypatch.setenv("OUROBOROS_MODEL", "gpt-5.4")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "gpt-5.4")
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "gpt-5.4")
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "gpt-5.4")
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACK_LIST", "gpt-5.4")

    class DummyResponse:
        def model_dump(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "total_tokens": 11,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            }

    class DummyResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return DummyResponse()

    dummy_client = types.SimpleNamespace(responses=DummyResponses())

    client = LLMClient(api_key="sk-test", base_url="http://127.0.0.1:2455/v1")
    monkeypatch.setattr(client, "_get_client", lambda: dummy_client)

    msg, usage = client.chat(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.4",
        prompt_cache_key="ouroboros:test-key",
    )

    assert msg["content"] == "ok"
    assert usage["prompt_tokens"] == 10
    assert usage["cached_tokens"] == 3
    assert captured["prompt_cache_key"] == "ouroboros:test-key"
    assert captured["prompt_cache_retention"] in {"in_memory", "in-memory"}


def test_llm_chat_keeps_prompt_cache_key_without_24h_for_non_gpt_5_4(monkeypatch):
    captured = {}
    monkeypatch.setenv("OUROBOROS_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACK_LIST", "gpt-5.3-codex")

    class DummyResponse:
        def model_dump(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 1,
                    "total_tokens": 9,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            }

    class DummyResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return DummyResponse()

    dummy_client = types.SimpleNamespace(responses=DummyResponses())

    client = LLMClient(api_key="sk-test", base_url="http://127.0.0.1:2455/v1")
    monkeypatch.setattr(client, "_get_client", lambda: dummy_client)

    client.chat(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.3-codex",
        prompt_cache_key="ouroboros:test-key",
    )

    assert captured["prompt_cache_key"] == "ouroboros:test-key"
    assert "prompt_cache_retention" not in captured


def test_llm_chat_uses_session_id_for_header_and_default_prompt_cache_key(monkeypatch):
    captured = {}

    class DummyResponse:
        def model_dump(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 1,
                    "total_tokens": 13,
                    "input_tokens_details": {"cached_tokens": 4},
                },
            }

    class DummyResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return DummyResponse()

    dummy_client = types.SimpleNamespace(responses=DummyResponses())

    client = LLMClient(api_key="sk-test", base_url="http://127.0.0.1:2455/v1")
    monkeypatch.setattr(client, "_get_client", lambda: dummy_client)

    client.chat(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.4",
        session_id="sess-task-1",
    )

    assert captured["prompt_cache_key"] == "sess-task-1"
    assert captured["extra_headers"]["session_id"] == "sess-task-1"


def test_llm_chat_maps_responses_function_calls_and_history(monkeypatch):
    captured = {}

    class DummyResponse:
        def model_dump(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "checking file"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "repo_read",
                        "arguments": "{\"path\":\"README.md\"}",
                    },
                ],
                "usage": {
                    "input_tokens": 21,
                    "output_tokens": 7,
                    "total_tokens": 28,
                    "input_tokens_details": {"cached_tokens": 5},
                },
            }

    class DummyResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return DummyResponse()

    dummy_client = types.SimpleNamespace(responses=DummyResponses())
    client = LLMClient(api_key="sk-test", base_url="http://127.0.0.1:2455/v1")
    monkeypatch.setattr(client, "_get_client", lambda: dummy_client)

    msg, usage = client.chat(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": "checking file",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "repo_read", "arguments": "{\"path\":\"README.md\"}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "README body"},
        ],
        model="gpt-5.4",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "repo_read",
                    "description": "Read file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ],
        prompt_cache_key="ouroboros:test-key",
    )

    assert msg["content"] == "checking file"
    assert msg["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "repo_read", "arguments": "{\"path\":\"README.md\"}"},
        }
    ]
    assert usage["prompt_tokens"] == 21
    assert usage["completion_tokens"] == 7
    assert usage["cached_tokens"] == 5

    tool = captured["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "repo_read"
    assert tool["description"] == "Read file"

    assert captured["instructions"] == "sys"
    assert captured["input"][0]["role"] == "user"
    assert captured["input"][1]["role"] == "assistant"
    assert captured["input"][2]["type"] == "function_call"
    assert captured["input"][3]["type"] == "function_call_output"
