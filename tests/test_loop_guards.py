from __future__ import annotations

from collections import deque

from ouroboros.loop import (
    _batch_tool_signature,
    _should_stop_on_repeated_signature,
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
