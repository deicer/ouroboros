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


def test_repo_read_maps_legacy_app_tools_path_to_package_layout():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros" / "tools").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "tools" / "sample.py"
        fp.write_text("print('ok')", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "/app/tools/sample.py")
        assert "print('ok')" in out


def test_repo_read_directory_returns_helpful_message():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "docs").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, str(repo / "docs"))
        assert "expects a file" in out
        assert "repo_list" in out


def test_repo_read_relative_tools_path_maps_to_ouroboros_package():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros" / "tools").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "tools" / "whisper.py"
        fp.write_text("# whisper tool", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "tools/whisper.py")
        assert "# whisper tool" in out


def test_repo_read_relative_loop_path_maps_to_ouroboros_package():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        (repo / "ouroboros").mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        fp = repo / "ouroboros" / "loop.py"
        fp.write_text("print('loop')", encoding="utf-8")

        ctx = _mk_ctx(repo, drive)
        out = _repo_read(ctx, "loop.py")
        assert "print('loop')" in out
