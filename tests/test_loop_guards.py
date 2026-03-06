from __future__ import annotations

from collections import deque

from ouroboros.loop import (
    _is_paid_limit_error,
    _batch_tool_signature,
    _loop_guard_hard_stop,
    _should_stop_on_repeated_signature,
    _tool_result_is_error,
)


def test_batch_tool_signature_stable_for_parallel_order():
    llm_trace = {
        "tool_calls": [
            {
                "tool": "repo_read",
                "args": {"path": "a.py"},
                "result": "ok-a",
                "is_error": False,
            },
            {
                "tool": "run_shell",
                "args": {"cmd": ["echo", "x"]},
                "result": "ok-b",
                "is_error": False,
            },
        ]
    }
    sig1 = _batch_tool_signature(llm_trace, batch_size=2)

    llm_trace_reordered = {
        "tool_calls": [
            {
                "tool": "run_shell",
                "args": {"cmd": ["echo", "x"]},
                "result": "ok-b",
                "is_error": False,
            },
            {
                "tool": "repo_read",
                "args": {"path": "a.py"},
                "result": "ok-a",
                "is_error": False,
            },
        ]
    }
    sig2 = _batch_tool_signature(llm_trace_reordered, batch_size=2)
    assert sig1 == sig2


def test_should_stop_on_repeated_signature_threshold():
    recent = deque(["A"] * 7 + ["B"], maxlen=12)
    stop, sig, count = _should_stop_on_repeated_signature(
        recent,
        threshold=7,
        window_min=8,
    )
    assert stop is True
    assert sig == "A"
    assert count == 7


def test_should_not_stop_if_window_too_small():
    recent = deque(["A"] * 5, maxlen=12)
    stop, _sig, _count = _should_stop_on_repeated_signature(
        recent,
        threshold=4,
        window_min=6,
    )
    assert stop is False


def test_tool_result_is_error_for_run_shell_nonzero_exit():
    assert _tool_result_is_error("run_shell", "exit_code=1\nboom") is True


def test_tool_result_is_not_error_for_run_shell_zero_exit():
    assert _tool_result_is_error("run_shell", "exit_code=0\nok") is False


def test_loop_guard_hard_stop_forces_stop_in_direct_chat_even_on_free_model():
    assert _loop_guard_hard_stop("arcee-ai/trinity-large-preview:free", is_direct_chat=True) is True


def test_loop_guard_hard_stop_keeps_default_behavior_outside_direct_chat():
    assert _loop_guard_hard_stop("arcee-ai/trinity-large-preview:free", is_direct_chat=False) is False


def test_is_paid_limit_error_detects_openrouter_key_limit_exceeded():
    assert _is_paid_limit_error(RuntimeError("Key limit exceeded (total limit).")) is True
