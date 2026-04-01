from __future__ import annotations

from supervisor.telegram import _progress_dedup_decision, _sanitize_owner_facing_text


def test_progress_dedup_suppresses_same_text_within_window():
    state = {}
    suppress1, state, count1 = _progress_dedup_decision(
        state,
        "hello world",
        now_epoch=1000.0,
        window_sec=60,
    )
    suppress2, state, count2 = _progress_dedup_decision(
        state,
        "hello world",
        now_epoch=1005.0,
        window_sec=60,
    )

    assert suppress1 is False
    assert suppress2 is True
    assert count1 == 0
    assert count2 == 1


def test_progress_dedup_allows_after_window():
    state = {}
    _progress_dedup_decision(state, "same", now_epoch=1000.0, window_sec=60)
    suppress, _state, count = _progress_dedup_decision(
        state,
        "same",
        now_epoch=1070.0,
        window_sec=60,
    )
    assert suppress is False
    assert count == 0


def test_progress_dedup_allows_different_text():
    state = {}
    _progress_dedup_decision(state, "first", now_epoch=1000.0, window_sec=60)
    suppress, _state, _count = _progress_dedup_decision(
        state,
        "second",
        now_epoch=1001.0,
        window_sec=60,
    )
    assert suppress is False


def test_sanitize_owner_facing_text_removes_raw_tool_leakage():
    raw = (
        "Сейчас добираю точку отправки.\n"
        "to=functions.run_shell {\"cmd\":[\"bash\",\"-lc\",\"python ...\"]}\n"
        "{\"cmd\":[\"bash\",\"-lc\",\"python ...\"]}\n"
        "Сразу после этого внесу патч.\n"
    )

    cleaned = _sanitize_owner_facing_text(raw)

    assert "to=functions.run_shell" not in cleaned
    assert "{\"cmd\"" not in cleaned
    assert "Сейчас добираю точку отправки." in cleaned
    assert "Сразу после этого внесу патч." in cleaned


def test_sanitize_owner_facing_text_collapses_duplicate_lines():
    raw = (
        "Смотрю конец send_with_budget и место, где сейчас реально уходит Telegram API.\n"
        "Смотрю конец send_with_budget и место, где сейчас реально уходит Telegram API.\n"
        "Смотрю конец send_with_budget и место, где сейчас реально уходит Telegram API.\n"
        "Потом внесу правку.\n"
    )

    cleaned = _sanitize_owner_facing_text(raw)

    assert cleaned.count("Смотрю конец send_with_budget") == 1
    assert "Потом внесу правку." in cleaned
