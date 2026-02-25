import pytest

from ouroboros.tools.registry import ToolContext
from ouroboros.utils import safe_relpath, safe_resolve_under_root


def test_safe_relpath_rejects_url_encoded_traversal():
    with pytest.raises(ValueError):
        safe_relpath("%2e%2e/%2e%2e/etc/passwd")


def test_safe_relpath_rejects_null_byte():
    with pytest.raises(ValueError):
        safe_relpath("memory/scratchpad.md\x00evil")


def test_safe_resolve_under_root_blocks_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        safe_resolve_under_root(root, "link/secret.txt")


def test_toolcontext_repo_path_blocks_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    drive = tmp_path / "data"
    out = tmp_path / "outside"
    repo.mkdir()
    drive.mkdir()
    out.mkdir()
    (repo / "escape").symlink_to(out, target_is_directory=True)

    ctx = ToolContext(repo_dir=repo, drive_root=drive)
    with pytest.raises(ValueError):
        ctx.repo_path("escape/file.txt")
