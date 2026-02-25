import sys
from pathlib import Path

from ouroboros.tools.registry import ToolContext
from ouroboros.tools.shell import _run_shell


def _ctx(tmp_path: Path) -> ToolContext:
    repo = tmp_path / "repo"
    drive = tmp_path / "data"
    repo.mkdir(parents=True, exist_ok=True)
    drive.mkdir(parents=True, exist_ok=True)
    return ToolContext(repo_dir=repo, drive_root=drive)


def test_run_shell_respects_timeout_env(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setenv("OUROBOROS_RUN_SHELL_TIMEOUT_SEC", "1")
    out = _run_shell(ctx, [sys.executable, "-c", "import time; time.sleep(2)"])
    assert "⚠️ TIMEOUT" in out
    assert "1s" in out


def test_run_shell_caps_output_to_100kb(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setenv("OUROBOROS_RUN_SHELL_MAX_OUTPUT_BYTES", "100000")
    out = _run_shell(ctx, [sys.executable, "-c", "print('x' * 250000)"])
    assert out.startswith("exit_code=0")
    assert "truncated at 100000 bytes" in out


def test_run_shell_blocks_escaped_cwd(tmp_path):
    ctx = _ctx(tmp_path)
    out = _run_shell(ctx, ["pwd"], cwd="../")
    assert "⚠️ PATH_ERROR" in out
