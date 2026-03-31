from __future__ import annotations

import json

from ouroboros.agent import (
    Env,
    OuroborosAgent,
    _is_status_template_reply,
    _response_focus_overlap_ratio,
    _sanitize_user_reply_for_delivery,
    _split_user_and_log_text,
    _strip_background_preamble_for_user,
    _text_similarity,
)
from ouroboros.llm import build_response_session_id


def test_loop_guard_message_is_short_russian_for_user():
    source = (
        "⚠️ Stuck in repeated identical tool batches (10 repeats in last 10 rounds). "
        "Recurring signature: repo_read|args=... Stopping to avoid loop."
    )
    user_text, log_text = _split_user_and_log_text(source)
    assert user_text.startswith("⚠️ Обнаружен цикл")
    assert "repeated identical tool batches" in log_text


def test_regular_message_is_unchanged():
    source = "Готово. Изменения применены."
    user_text, log_text = _split_user_and_log_text(source)
    assert user_text == source
    assert log_text == source


def test_strip_background_preamble_keeps_explicit_answer_for_user():
    source = (
        "## Фоновый цикл: голосовые сообщения\n\n"
        "Технический отчёт...\n\n"
        "## Ответ: Голосовые сообщения\n\n"
        "Сначала нужно сделать Docker rebuild."
    )
    out = _strip_background_preamble_for_user(source, task={"text": "научись читать мои голосовые сообщения"})
    assert out.startswith("## Ответ: Голосовые сообщения")
    assert "Фоновый цикл" not in out


def test_strip_background_preamble_keeps_auto_resume_status_intact():
    source = (
        "## Фоновый цикл: мысли\n\n"
        "Статус после рестарта."
    )
    out = _strip_background_preamble_for_user(source, task={"text": "[auto-resume after restart] continue"})
    assert out == source


def test_strip_background_preamble_fallback_when_no_answer_marker():
    source = "## Фоновый цикл: мысли\n\nТолько внутренний отчёт."
    out = _strip_background_preamble_for_user(source, task={"text": "обычный вопрос"})
    assert out.startswith("Понял. Убрал внутренний технический отчёт.")
    assert "⚠️ Внутренний отчёт не должен был попасть в чат" not in out
    assert "обычный вопрос" in out


def test_status_template_detection_and_similarity():
    a = "## Фактологичный апдейт (16:38 UTC)\n\n**Текущее состояние:**\n- Бюджет: $0.01"
    b = "## Фактологичный апдейт (16:39 UTC)\n\n**Текущее состояние:**\n- Бюджет: $0.01"
    assert _is_status_template_reply(a) is True
    assert _text_similarity(a, b) > 0.88


def test_focus_overlap_ratio_for_direct_question():
    question = "в чем у тебя проблема? почему так долго решаешь простую задачу?"
    stale = "## Фактологичный апдейт\n\nТекущее состояние: бюджет, e2e, loop.py, git."
    assert _response_focus_overlap_ratio(stale, question) < 0.45


def test_relevance_guard_rewrites_repeated_template(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (drive_root / "memory" / "identity.md").write_text("# id", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("", encoding="utf-8")
    (drive_root / "logs" / "chat.jsonl").write_text(
        '{"direction":"out","chat_id":127020942,"text":"## Фактологичный апдейт (16:38 UTC)\\n\\nСтарый шаблон"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(OuroborosAgent, "_log_worker_boot_once", lambda self: None)
    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    agent = OuroborosAgent(env=env, event_queue=None)

    monkeypatch.setattr(
        agent,
        "_rewrite_user_reply_for_relevance",
        lambda current_text, task_text: ("Отвечаю по сути: проблема в повторе шаблона, исправляю.", {}),
    )

    usage_total = {}
    out = agent._apply_response_relevance_guard(
        "## Фактологичный апдейт (16:39 UTC)\n\nСтарый шаблон",
        task={
            "id": "t1",
            "chat_id": 127020942,
            "text": "а в чем у тебя проблема? почему так долго решаешь простую задачу?",
        },
        usage_total=usage_total,
        drive_logs=drive_root / "logs",
    )
    assert out.startswith("Отвечаю по сути:")


def test_emit_task_results_applies_fact_verification_gate(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (drive_root / "memory" / "identity.md").write_text("# id", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("", encoding="utf-8")

    monkeypatch.setattr(OuroborosAgent, "_log_worker_boot_once", lambda self: None)
    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    agent = OuroborosAgent(env=env, event_queue=None)

    monkeypatch.setattr(
        "ouroboros.agent.apply_fact_verification_gate",
        lambda text, **_: "[FACT_GATE]\n" + text,
    )

    agent._emit_task_results(
        task={"id": "t1", "chat_id": 1, "text": "status?"},
        text="Все изменения зафиксированы в git (commit 52ad6db).",
        usage={},
        llm_trace={"tool_calls": []},
        start_time=0.0,
        drive_logs=drive_root / "logs",
    )

    send_events = [e for e in agent._pending_events if e.get("type") == "send_message"]
    assert send_events
    assert send_events[-1]["text"].startswith("[FACT_GATE]")


def test_sanitize_user_reply_for_delivery_blocks_raw_cursor_json():
    out = _sanitize_user_reply_for_delivery('{"id":"0f2a6d5c","cursor":"0","loc":0}')
    assert "служебный payload" in out.lower()
    assert "cursor" not in out


def test_sanitize_user_reply_for_delivery_blocks_internal_draft():
    source = (
        "Let's stop. The last assistant message was empty.\n"
        "Need to inspect owner interrupt handling.\n"
        "Use run_shell to grep owner_interrupt.\n"
        "Then we need to plan tests in tests/test_loop_guards.py.\n"
        "After we get output, we proceed.\n"
        "Use repo_read for loop.py and run_shell for sed -n.\n"
    )
    out = _sanitize_user_reply_for_delivery(source)
    assert "внутренний рабочий черновик" in out.lower()
    assert "run_shell" not in out


def test_emit_task_results_sanitizes_raw_json_payload(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (drive_root / "memory" / "identity.md").write_text("# id", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("", encoding="utf-8")

    monkeypatch.setattr(OuroborosAgent, "_log_worker_boot_once", lambda self: None)
    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    agent = OuroborosAgent(env=env, event_queue=None)

    monkeypatch.setattr(
        "ouroboros.agent.apply_fact_verification_gate",
        lambda text, **_: text,
    )

    agent._emit_task_results(
        task={"id": "t2", "chat_id": 1, "text": "status?"},
        text='{"id":"0f2a6d5c","cursor":"0","loc":0}',
        usage={},
        llm_trace={"tool_calls": []},
        start_time=0.0,
        drive_logs=drive_root / "logs",
    )

    send_events = [e for e in agent._pending_events if e.get("type") == "send_message"]
    assert send_events
    assert "служебный payload" in send_events[-1]["text"].lower()


def test_emit_task_results_skips_owner_delivery_for_silent_subtask(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (drive_root / "memory" / "identity.md").write_text("# id", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("", encoding="utf-8")

    monkeypatch.setattr(OuroborosAgent, "_log_worker_boot_once", lambda self: None)
    monkeypatch.setattr("ouroboros.agent.apply_fact_verification_gate", lambda text, **_: text)
    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    agent = OuroborosAgent(env=env, event_queue=None)

    agent._emit_task_results(
        task={"id": "t3", "chat_id": 1, "text": "subtask", "_silent_subtask": True},
        text="internal subtask result",
        usage={},
        llm_trace={"tool_calls": []},
        start_time=0.0,
        drive_logs=drive_root / "logs",
    )

    send_events = [e for e in agent._pending_events if e.get("type") == "send_message"]
    assert send_events == []


def test_rewrite_user_reply_uses_prompt_cache_key(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    drive_root = tmp_path / "drive"
    repo_dir.mkdir()
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "memory").mkdir(parents=True)
    (drive_root / "state").mkdir(parents=True)
    (drive_root / "memory" / "identity.md").write_text("# id", encoding="utf-8")
    (drive_root / "memory" / "scratchpad.md").write_text("", encoding="utf-8")
    (drive_root / "state" / "state.json").write_text(
        json.dumps({"session_id": "sess-agent"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(OuroborosAgent, "_log_worker_boot_once", lambda self: None)
    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    agent = OuroborosAgent(env=env, event_queue=None)

    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"content": "Переписанный ответ."}, {"prompt_tokens": 10, "completion_tokens": 3}

    monkeypatch.setattr(agent.llm, "chat", fake_chat)

    out, _usage = agent._rewrite_user_reply_for_relevance(
        "Шаблонный статус",
        task_text="Почему так долго?",
    )

    expected_key = build_response_session_id(
        scope="reply_rewrite",
        runtime_session_id="sess-agent",
    )
    assert out == "Переписанный ответ."
    assert captured["prompt_cache_key"] == expected_key
    assert captured["session_id"] == expected_key
