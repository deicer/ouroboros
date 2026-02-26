from __future__ import annotations

from ouroboros.agent import (
    Env,
    OuroborosAgent,
    _is_status_template_reply,
    _response_focus_overlap_ratio,
    _split_user_and_log_text,
    _strip_background_preamble_for_user,
    _text_similarity,
)


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
