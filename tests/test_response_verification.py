from __future__ import annotations

from ouroboros.response_verification import apply_fact_verification_gate


def test_fact_gate_marks_missing_commit_as_unverified(tmp_path):
    text = "Все изменения зафиксированы в git (commit 52ad6db)."

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace={},
    )

    assert "Автопроверка фактов" in out
    assert "52ad6db" in out
    assert "не подтверждено" in out


def test_fact_gate_marks_missing_file_as_unverified(tmp_path):
    text = "Создан модуль ouroboros/tools/voice/voice.py с полной функциональностью."

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace={},
    )

    assert "Автопроверка фактов" in out
    assert "ouroboros/tools/voice/voice.py" in out
    assert "не подтверждено" in out


def test_fact_gate_requires_evidence_for_tests_passed_claim(tmp_path):
    text = "Все тесты прошли успешно."

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace={"tool_calls": []},
    )

    assert "Автопроверка фактов" in out
    assert "тест" in out.lower()
    assert "не подтверждено" in out


def test_fact_gate_keeps_text_when_claims_are_supported(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    voice_file = repo / "ouroboros" / "tools" / "voice" / "voice.py"
    voice_file.parent.mkdir(parents=True, exist_ok=True)
    voice_file.write_text("# voice tool", encoding="utf-8")

    text = "Создан модуль ouroboros/tools/voice/voice.py. Тесты прошли успешно."
    llm_trace = {
        "tool_calls": [
            {
                "tool": "run_shell",
                "is_error": False,
                "result": "pytest -q\n2 passed in 0.12s",
            }
        ]
    }

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=repo,
        llm_trace=llm_trace,
    )

    assert out == text


def test_fact_gate_accepts_absolute_repo_path_claim(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir(parents=True, exist_ok=True)
    smoke_file = repo / "opencode_smoke_test.txt"
    smoke_file.write_text("ok\n", encoding="utf-8")

    text = "Создан файл /app/opencode_smoke_test.txt."

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=repo,
        llm_trace={},
    )

    assert out == text


def test_fact_gate_accepts_successful_run_shell_output_without_pytest_word(tmp_path):
    text = "Все тесты прошли успешно."
    llm_trace = {
        "tool_calls": [
            {
                "tool": "run_shell",
                "is_error": False,
                "result": "exit_code=0\n34 passed in 1.21s",
            }
        ]
    }

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace=llm_trace,
    )

    assert out == text


def test_fact_gate_accepts_successful_output_with_zero_errors(tmp_path):
    text = "Все тесты прошли успешно."
    llm_trace = {
        "tool_calls": [
            {
                "tool": "run_shell",
                "is_error": False,
                "result": "exit_code=0\nRan 12 tests in 0.10s\nOK\n0 errors",
            }
        ]
    }

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace=llm_trace,
    )

    assert out == text


def test_fact_gate_ignores_negative_not_all_tests_passed_statement(tmp_path):
    text = "Не все тесты прошли, есть падения."

    out = apply_fact_verification_gate(
        text=text,
        repo_dir=tmp_path,
        llm_trace={"tool_calls": []},
    )

    assert out == text
