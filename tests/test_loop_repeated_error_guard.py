from ouroboros.loop import _batch_error_signature


def test_batch_error_signature_empty_when_no_errors():
    trace = {
        "tool_calls": [
            {"tool": "repo_list", "result": "ok", "is_error": False},
            {"tool": "repo_read", "result": "content", "is_error": False},
        ]
    }
    assert _batch_error_signature(trace, 2) == ""


def test_batch_error_signature_stable_and_deduplicated():
    trace = {
        "tool_calls": [
            {"tool": "run_shell", "result": "exit_code=1\nNameError", "is_error": True},
            {"tool": "run_shell", "result": "exit_code=1\nNameError", "is_error": True},
            {"tool": "whisper_transcribe", "result": "⚠️ Unknown tool: whisper_transcribe", "is_error": True},
        ]
    }
    sig = _batch_error_signature(trace, 3)
    assert "run_shell:" in sig
    assert "whisper_transcribe:" in sig
    # run_shell duplicate error should appear once in signature
    assert sig.count("run_shell:") == 1

