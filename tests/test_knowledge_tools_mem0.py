import pathlib
import tempfile

from ouroboros.memory_backends import FileKnowledgeBackend, KnowledgeBackend
from ouroboros.tools.knowledge import _knowledge_list, _knowledge_write
from ouroboros.tools.registry import ToolContext


class _BoomBackend(KnowledgeBackend):
    def read(self, topic: str) -> str:
        raise RuntimeError("mem0 down")

    def write(self, topic: str, content: str, mode: str = "overwrite") -> str:
        raise RuntimeError("mem0 down")

    def list_topics(self) -> str:
        raise RuntimeError("mem0 down")


def _mk_ctx(repo_dir: pathlib.Path, drive_root: pathlib.Path) -> ToolContext:
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def test_knowledge_write_falls_back_to_file_backend_when_primary_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)
        fallback = FileKnowledgeBackend(ctx)

        monkeypatch.setattr(
            "ouroboros.tools.knowledge._get_backend_bundle",
            lambda _ctx: (_BoomBackend(), fallback, None),
        )

        out = _knowledge_write(ctx, topic="python", content="hello", mode="overwrite")
        stored = (drive / "memory" / "knowledge" / "python.md").read_text(encoding="utf-8")

        assert "fallback=file" in out
        assert stored == "hello"


def test_knowledge_list_includes_init_warning_prefix(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)
        fallback = FileKnowledgeBackend(ctx)
        fallback.write(topic="python", content="hint", mode="overwrite")

        monkeypatch.setattr(
            "ouroboros.tools.knowledge._get_backend_bundle",
            lambda _ctx: (fallback, fallback, "⚠️ mem0 unavailable, fallback=file (init)"),
        )

        out = _knowledge_list(ctx)

        assert out.startswith("⚠️ mem0 unavailable, fallback=file (init)")
        assert "- **python**:" in out
