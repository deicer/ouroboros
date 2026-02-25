import pathlib
import tempfile


def test_budget_guard_ignores_free_model_by_default():
    from ouroboros.loop import _check_budget_limits

    with tempfile.TemporaryDirectory() as tmp:
        result = _check_budget_limits(
            budget_remaining_usd=1.0,
            accumulated_usage={"cost": 999.0},
            round_idx=1,
            messages=[],
            llm=object(),
            active_model="arcee-ai/trinity-large-preview:free",
            active_effort="low",
            max_retries=1,
            drive_logs=pathlib.Path(tmp),
            task_id="t-free",
            event_queue=None,
            llm_trace={"assistant_notes": [], "tool_calls": []},
            task_type="task",
        )
    assert result is None
