import pathlib
from types import SimpleNamespace


def _ctx(tmp_path):
    return SimpleNamespace(
        repo_dir=pathlib.Path(tmp_path),
        branch_dev="ouroboros",
        pending_events=[],
        last_push_succeeded=False,
    )


def test_git_push_with_tests_strict_blocks(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    calls = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(list(cmd))
        return ""

    monkeypatch.setenv("OUROBOROS_PRE_PUSH_STRICT", "1")
    monkeypatch.setattr(git_tools, "_run_pre_push_tests", lambda ctx: "failing test")
    monkeypatch.setattr(git_tools, "run_cmd", fake_run_cmd)

    ok, msg = git_tools._git_push_with_tests(_ctx(tmp_path))
    assert ok is False
    assert "PRE_PUSH_TESTS_FAILED" in msg
    assert not any(cmd[:2] == ["git", "push"] for cmd in calls)


def test_git_push_with_tests_non_strict_allows_push(monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools

    calls = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(list(cmd))
        return ""

    monkeypatch.setenv("OUROBOROS_PRE_PUSH_STRICT", "0")
    monkeypatch.setattr(git_tools, "_run_pre_push_tests", lambda ctx: "failing test")
    monkeypatch.setattr(git_tools, "run_cmd", fake_run_cmd)

    ctx = _ctx(tmp_path)
    ok, msg = git_tools._git_push_with_tests(ctx)
    assert ok is True
    assert "NON_BLOCKING" in msg
    assert any(cmd[:2] == ["git", "push"] for cmd in calls)
    assert any(ev.get("type") == "pre_push_tests_failed_non_blocking" for ev in ctx.pending_events)
