import time


def test_pick_initial_model_prefers_free_for_regular_task(monkeypatch, tmp_path):
    from ouroboros.loop import _pick_initial_model

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.setenv("OUROBOROS_MODEL", "x-ai/grok-4.1-fast")
    monkeypatch.delenv("OUROBOROS_MODEL_CODE", raising=False)
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "x-ai/grok-4.1-fast,anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("OUROBOROS_MODEL_FREE_LIST", "z-ai/glm-4.5-air:free,arcee-ai/trinity-large-preview:free")

    model = _pick_initial_model("x-ai/grok-4.1-fast", task_type="task")
    assert model == "z-ai/glm-4.5-air:free"


def test_pick_initial_model_prefers_paid_for_complex_task(monkeypatch, tmp_path):
    from ouroboros.loop import _pick_initial_model

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.setenv("OUROBOROS_MODEL", "x-ai/grok-4.1-fast")
    monkeypatch.delenv("OUROBOROS_MODEL_CODE", raising=False)
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "x-ai/grok-4.1-fast,anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("OUROBOROS_MODEL_FREE_LIST", "z-ai/glm-4.5-air:free")

    model = _pick_initial_model("x-ai/grok-4.1-fast", task_type="review")
    assert model == "x-ai/grok-4.1-fast"


def test_pick_initial_model_uses_main_model_for_direct_chat(monkeypatch, tmp_path):
    from ouroboros.loop import _pick_initial_model

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.setenv("OUROBOROS_MODEL", "gpt-5.1-codex-mini")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "gpt-5.3-codex,gpt-5.2")
    monkeypatch.setenv("OUROBOROS_MODEL_FREE_LIST", "")

    model = _pick_initial_model(
        "gpt-5.1-codex-mini",
        task_type="task",
        is_direct_chat=True,
    )
    assert model == "gpt-5.1-codex-mini"


def test_pick_initial_model_uses_code_model_for_worker_tasks(monkeypatch, tmp_path):
    from ouroboros.loop import _pick_initial_model

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.setenv("OUROBOROS_MODEL", "gpt-5.1-codex-mini")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "gpt-5.3-codex")
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "gpt-5.3-codex,gpt-5.2")
    monkeypatch.setenv("OUROBOROS_MODEL_FREE_LIST", "")

    model = _pick_initial_model(
        "gpt-5.1-codex-mini",
        task_type="task",
        is_direct_chat=False,
    )
    assert model == "gpt-5.3-codex"


def test_next_paid_candidate_skips_cooldown(monkeypatch, tmp_path):
    from ouroboros.loop import _next_paid_candidate

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_ENV_FILE", str(env_file))
    monkeypatch.setenv("OUROBOROS_MODEL_PAID_LIST", "x-ai/grok-4.1-fast,anthropic/claude-sonnet-4.6")
    cooldown_until = {
        "x-ai/grok-4.1-fast": time.monotonic() + 120.0,
    }
    selected = _next_paid_candidate("z-ai/glm-4.5-air:free", cooldown_until)
    assert selected == "anthropic/claude-sonnet-4.6"


def test_paid_limit_error_detection():
    from ouroboros.loop import _is_paid_limit_error

    assert _is_paid_limit_error(RuntimeError("Payment required: insufficient credits"))
    assert not _is_paid_limit_error(RuntimeError("temporary network timeout"))


def test_no_distinct_fallback_message_for_openrouter_free():
    from ouroboros.loop import _no_distinct_fallback_message

    msg = _no_distinct_fallback_message("openrouter/free", 3)

    assert "openrouter/free" in msg
    assert "router" in msg.lower()
    assert "No distinct fallback model is configured." in msg


def test_no_distinct_fallback_message_for_regular_model():
    from ouroboros.loop import _no_distinct_fallback_message

    msg = _no_distinct_fallback_message("gpt-5.4", 3)

    assert "gpt-5.4" in msg
    assert "router" not in msg.lower()
    assert "No distinct fallback model is configured." in msg
