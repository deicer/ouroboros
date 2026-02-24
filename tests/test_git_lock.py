import pathlib
import tempfile

import pytest

from ouroboros.tools.git import _acquire_git_lock, _release_git_lock
from ouroboros.tools.registry import ToolContext


def _mk_ctx(repo: pathlib.Path, drive: pathlib.Path) -> ToolContext:
    return ToolContext(repo_dir=repo, drive_root=drive)


def test_acquire_git_lock_reclaims_dead_pid_lock(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "locks").mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)

        lock_path = drive / "locks" / "git.lock"
        lock_path.write_text("locked_at=2026-01-01T00:00:00Z\npid=999999\n", encoding="utf-8")

        # pid does not exist -> lock should be reclaimed immediately.
        monkeypatch.setattr("ouroboros.tools.git._pid_is_alive", lambda pid: False)

        acquired = _acquire_git_lock(ctx, timeout_sec=2)
        try:
            content = acquired.read_text(encoding="utf-8")
            assert "pid=" in content
        finally:
            _release_git_lock(acquired)


def test_acquire_git_lock_waits_for_live_owner_and_times_out(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "locks").mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)

        lock_path = drive / "locks" / "git.lock"
        lock_path.write_text("locked_at=2026-01-01T00:00:00Z\npid=12345\n", encoding="utf-8")

        monkeypatch.setattr("ouroboros.tools.git._pid_is_alive", lambda pid: True)
        with pytest.raises(TimeoutError):
            _acquire_git_lock(ctx, timeout_sec=1)
