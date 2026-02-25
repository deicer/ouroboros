from __future__ import annotations

from ouroboros.agent import _strip_background_preamble_for_user, _split_user_and_log_text


def test_loop_guard_message_is_short_russian_for_user():
    source = (
        "⚠️ Stuck in repeated identical tool batches (10 repeats in last 10 rounds). "
        "Recurring signature: repo_read|args=... Stopping to avoid loop."
    )
    user_text, log_text = _split_user_and_log_text(source)
    assert user_text.startswith("⚠️ Я зациклился")
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
    assert out.startswith("⚠️ Внутренний отчёт")
