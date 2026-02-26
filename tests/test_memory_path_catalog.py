import pathlib
import tempfile

from ouroboros.memory import Memory


def test_refresh_path_catalog_includes_new_repo_file():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)

        mem = Memory(drive_root=drive, repo_dir=repo)
        mem.ensure_files()

        first = mem.refresh_path_catalog(reason="test-initial")
        assert "repo_files" in first

        new_file = repo / "ouroboros" / "new_module.py"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("x = 1\n", encoding="utf-8")

        second = mem.refresh_path_catalog(reason="test-after-new-file")
        assert "ouroboros/new_module.py" in set(second.get("repo_files", []))


def test_path_catalog_path_exists_after_refresh():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)

        mem = Memory(drive_root=drive, repo_dir=repo)
        mem.ensure_files()
        mem.refresh_path_catalog(reason="test")

        assert mem.path_catalog_path().exists()
