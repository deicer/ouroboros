import pathlib
import tempfile

from ouroboros.tools.registry import ToolContext


class _FakeMem0Client:
    def __init__(self):
        self.add_calls = []
        self.get_all_calls = []
        self.delete_calls = []
        self._responses = []

    def queue_get_all(self, payload):
        self._responses.append(payload)

    def add(self, messages, **kwargs):
        self.add_calls.append({"messages": messages, **kwargs})
        return {"results": [{"id": "new", "memory": str(messages), "event": "ADD"}]}

    def get_all(self, **kwargs):
        self.get_all_calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return {"results": []}

    def delete(self, memory_id):
        self.delete_calls.append(memory_id)
        return {"message": "Memory deleted successfully!"}


def _mk_ctx(repo_dir: pathlib.Path, drive_root: pathlib.Path) -> ToolContext:
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def test_select_knowledge_backend_falls_back_to_file_when_google_key_missing(monkeypatch):
    from ouroboros.memory_backends import FileKnowledgeBackend, select_knowledge_backend

    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)

        monkeypatch.setenv("OUROBOROS_KNOWLEDGE_BACKEND", "mem0")
        monkeypatch.setenv("OUROBOROS_MEM0_ENABLED", "true")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        backend, warning = select_knowledge_backend(ctx)

        assert isinstance(backend, FileKnowledgeBackend)
        assert warning is not None
        assert "GOOGLE_API_KEY" in warning


def test_select_knowledge_backend_uses_mem0_when_configured(monkeypatch):
    from ouroboros.memory_backends import Mem0KnowledgeBackend, select_knowledge_backend

    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)

        fake = _FakeMem0Client()
        monkeypatch.setenv("OUROBOROS_KNOWLEDGE_BACKEND", "mem0")
        monkeypatch.setenv("OUROBOROS_MEM0_ENABLED", "true")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.setattr("ouroboros.memory_backends._create_mem0_client", lambda **_: fake)

        backend, warning = select_knowledge_backend(ctx)

        assert isinstance(backend, Mem0KnowledgeBackend)
        assert warning is None


def test_select_knowledge_backend_falls_back_when_mem0_init_fails(monkeypatch):
    from ouroboros.memory_backends import FileKnowledgeBackend, select_knowledge_backend

    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        drive = pathlib.Path(td) / "data"
        repo.mkdir(parents=True, exist_ok=True)
        drive.mkdir(parents=True, exist_ok=True)
        ctx = _mk_ctx(repo, drive)

        monkeypatch.setenv("OUROBOROS_KNOWLEDGE_BACKEND", "mem0")
        monkeypatch.setenv("OUROBOROS_MEM0_ENABLED", "true")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.setattr(
            "ouroboros.memory_backends._create_mem0_client",
            lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        backend, warning = select_knowledge_backend(ctx)

        assert isinstance(backend, FileKnowledgeBackend)
        assert warning is not None
        assert "fallback=file" in warning


def test_mem0_backend_append_calls_add_with_infer_and_topic_metadata():
    from ouroboros.memory_backends import Mem0KnowledgeBackend

    fake = _FakeMem0Client()
    backend = Mem0KnowledgeBackend(client=fake, user_id="agent", infer=True, max_memories=100)

    out = backend.write(topic="python", content="Use pytest fixtures", mode="append")

    assert out.startswith("✅ Knowledge 'python' saved")
    assert len(fake.add_calls) == 1
    add_call = fake.add_calls[0]
    assert add_call["messages"] == "Use pytest fixtures"
    assert add_call["infer"] is True
    assert add_call["user_id"] == "agent"
    assert add_call["metadata"]["topic"] == "python"


def test_mem0_backend_overwrite_deletes_existing_topic_then_adds():
    from ouroboros.memory_backends import Mem0KnowledgeBackend

    fake = _FakeMem0Client()
    fake.queue_get_all({"results": [{"id": "m1"}, {"id": "m2"}]})
    backend = Mem0KnowledgeBackend(client=fake, user_id="agent", infer=True, max_memories=100)

    out = backend.write(topic="python", content="Fresh memory", mode="overwrite")

    assert out.startswith("✅ Knowledge 'python' saved")
    assert fake.delete_calls == ["m1", "m2"]
    assert len(fake.add_calls) == 1
    assert fake.add_calls[0]["messages"] == "Fresh memory"


def test_mem0_backend_read_returns_not_found_message_when_topic_empty():
    from ouroboros.memory_backends import Mem0KnowledgeBackend

    fake = _FakeMem0Client()
    fake.queue_get_all({"results": []})
    backend = Mem0KnowledgeBackend(client=fake, user_id="agent", infer=False, max_memories=100)

    out = backend.read(topic="missing")

    assert "Topic 'missing' not found" in out


def test_mem0_backend_list_topics_builds_markdown_index():
    from ouroboros.memory_backends import Mem0KnowledgeBackend

    fake = _FakeMem0Client()
    fake.queue_get_all(
        {
            "results": [
                {"id": "1", "memory": "First line", "metadata": {"topic": "python"}},
                {"id": "2", "memory": "Second line", "metadata": {"topic": "python"}},
                {"id": "3", "memory": "Browser recipe", "metadata": {"topic": "playwright"}},
            ]
        }
    )
    backend = Mem0KnowledgeBackend(client=fake, user_id="agent", infer=False, max_memories=100)

    out = backend.list_topics()

    assert out.startswith("# Knowledge Base Index")
    assert "- **python**:" in out
    assert "- **playwright**:" in out
