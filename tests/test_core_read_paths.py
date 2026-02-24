import pathlib
import tempfile

from ouroboros.tools.core import _drive_read, _repo_read
from ouroboros.tools.registry import ToolContext


def _mk_ctx(repo_dir: pathlib.Path, drive_root: pathlib.Path) -> ToolContext:
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def test_drive_read_accepts_absolute_data_path():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        (drive / "memory").mkdir(parents=True, exist_ok=True)
        fp = drive / "memory" / "scratchpad.md"
        fp.write_text("hello-drive", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _drive_read(ctx, str(fp))
        assert out == "hello-drive"


def test_repo_read_accepts_absolute_app_path_and_limit():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "AGENT.md"
        fp.write_text("abcdef", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, str(fp), limit=3)
        assert out.startswith("abc")
        assert "truncated at 3 chars" in out
