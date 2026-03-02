"""Knowledge backends for tool-level memory storage.

Supports:
- file backend (`memory/knowledge/*.md`) as stable fallback
- Mem0 backend (Gemini + Qdrant) for semantic memory
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlparse

from ouroboros.tools.registry import ToolContext

log = logging.getLogger(__name__)

KNOWLEDGE_DIR = "memory/knowledge"
INDEX_FILE = "_index.md"


class KnowledgeBackend(Protocol):
    def read(self, topic: str) -> str:
        ...

    def write(self, topic: str, content: str, mode: str = "overwrite") -> str:
        ...

    def list_topics(self) -> str:
        ...


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int, min_value: int = 1, max_value: int = 10_000) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip()) if raw is not None else int(default)
    except Exception:
        value = int(default)
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _build_fallback_warning(reason: str) -> str:
    return f"⚠️ mem0 unavailable, fallback=file ({reason})"


@dataclass
class FileKnowledgeBackend:
    ctx: ToolContext

    def _kdir(self) -> Path:
        return self.ctx.drive_path(KNOWLEDGE_DIR)

    def _topic_path(self, topic: str) -> Path:
        kdir = self._kdir()
        path = (kdir / f"{topic}.md").resolve()
        kdir_resolved = kdir.resolve()
        try:
            path.relative_to(kdir_resolved)
        except ValueError as exc:
            raise ValueError(f"Path escape detected: {topic}") from exc
        return path

    def _ensure_dir(self) -> None:
        self._kdir().mkdir(parents=True, exist_ok=True)

    def _extract_summary(self, text: str, max_chars: int = 150) -> str:
        lines = text.strip().split("\n")
        snippets: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            clean = stripped.lstrip("-*").strip().lstrip("#").strip()
            if clean:
                snippets.append(clean)
            if len(snippets) >= 3:
                break
        summary = " | ".join(snippets)
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1] + "…"
        return summary

    def _update_index_entry(self, topic: str) -> None:
        kdir = self._kdir()
        index_path = kdir / INDEX_FILE
        topic_path = kdir / f"{topic}.md"
        self._ensure_dir()

        if index_path.exists():
            index_content = index_path.read_text(encoding="utf-8")
        else:
            index_content = "# Knowledge Base Index\n\n"

        lines = index_content.split("\n")
        header_end = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                header_end = i + 1
                if i + 1 < len(lines) and lines[i + 1].strip() == "":
                    header_end = i + 2
                break

        header = "\n".join(lines[:header_end])
        entries = [line for line in lines[header_end:] if line.strip() and line.strip() != "(empty)"]

        pattern = f"- **{topic}**:"
        entries = [e for e in entries if not e.strip().startswith(pattern)]

        if topic_path.exists():
            try:
                text = topic_path.read_text(encoding="utf-8").strip()
                summary = self._extract_summary(text)
                new_entry = f"- **{topic}**: {summary}"
            except Exception:
                log.debug("Failed to read knowledge file for index update: %s", topic, exc_info=True)
                new_entry = f"- **{topic}**: (unreadable)"

            entries.append(new_entry)
            entries.sort(key=lambda e: e.lower())

        if entries:
            new_index = header.rstrip("\n") + "\n\n" + "\n".join(entries) + "\n"
        else:
            new_index = header.rstrip("\n") + "\n\n(empty)\n"

        temp_path = index_path.with_suffix(".tmp")
        temp_path.write_text(new_index, encoding="utf-8")
        temp_path.replace(index_path)

    def _rebuild_index(self) -> None:
        kdir = self._kdir()
        if not kdir.exists():
            return

        entries = []
        for f in sorted(kdir.glob("*.md")):
            if f.name == INDEX_FILE:
                continue
            topic = f.stem
            try:
                text = f.read_text(encoding="utf-8").strip()
                summary = self._extract_summary(text)
                entries.append(f"- **{topic}**: {summary}")
            except Exception:
                log.debug("Failed to read knowledge file for index rebuild: %s", topic, exc_info=True)
                entries.append(f"- **{topic}**: (unreadable)")

        index_content = "# Knowledge Base Index\n\n"
        if entries:
            index_content += "\n".join(entries) + "\n"
        else:
            index_content += "(empty)\n"

        (kdir / INDEX_FILE).write_text(index_content, encoding="utf-8")

    def read(self, topic: str) -> str:
        path = self._topic_path(topic)
        if not path.exists():
            return f"Topic '{topic}' not found. Use knowledge_list to see available topics."
        return path.read_text(encoding="utf-8")

    def write(self, topic: str, content: str, mode: str = "overwrite") -> str:
        path = self._topic_path(topic)
        self._ensure_dir()

        if mode == "append":
            needs_newline = False
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as rf:
                    rf.seek(-1, 2)
                    if rf.read(1) != b"\n":
                        needs_newline = True
            with open(path, "a", encoding="utf-8") as f:
                if needs_newline:
                    f.write("\n")
                f.write(content)
        else:
            path.write_text(content, encoding="utf-8")

        self._update_index_entry(topic)
        return f"✅ Knowledge '{topic}' saved ({mode})."

    def list_topics(self) -> str:
        kdir = self._kdir()
        index_path = kdir / INDEX_FILE
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        if kdir.exists():
            self._rebuild_index()
            if index_path.exists():
                return index_path.read_text(encoding="utf-8")
        return "Knowledge base is empty. Use knowledge_write to add topics."


@dataclass
class Mem0KnowledgeBackend:
    client: Any
    user_id: str
    infer: bool = True
    max_memories: int = 500

    @staticmethod
    def _extract_results(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            results = payload.get("results", [])
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict)]
            return []
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    @staticmethod
    def _extract_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        # Fallback: in some payloads metadata-like fields are promoted to top-level.
        excluded = {"id", "memory", "hash", "score", "created_at", "updated_at", "user_id", "agent_id", "run_id"}
        promoted = {k: v for k, v in item.items() if k not in excluded}
        return promoted if promoted else {}

    @staticmethod
    def _extract_memory_text(item: Dict[str, Any]) -> str:
        text = item.get("memory", "")
        if text:
            return str(text)
        fallback = item.get("data", "")
        return str(fallback or "")

    @staticmethod
    def _extract_ts(item: Dict[str, Any]) -> str:
        return str(item.get("created_at") or item.get("updated_at") or "")

    @staticmethod
    def _extract_summary(text: str, max_chars: int = 150) -> str:
        lines = text.strip().split("\n")
        snippets: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            clean = stripped.lstrip("-*").strip().lstrip("#").strip()
            if clean:
                snippets.append(clean)
            if len(snippets) >= 3:
                break
        summary = " | ".join(snippets)
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1] + "…"
        return summary

    def _topic_entries(self, topic: str) -> List[Dict[str, Any]]:
        payload = self.client.get_all(
            user_id=self.user_id,
            filters={"topic": topic},
            limit=self.max_memories,
        )
        entries = self._extract_results(payload)
        entries.sort(key=self._extract_ts)
        return entries

    def read(self, topic: str) -> str:
        entries = self._topic_entries(topic)
        if not entries:
            return f"Topic '{topic}' not found. Use knowledge_list to see available topics."
        texts = [self._extract_memory_text(entry).strip() for entry in entries]
        texts = [t for t in texts if t]
        if not texts:
            return f"Topic '{topic}' not found. Use knowledge_list to see available topics."
        return "\n\n".join(texts)

    def write(self, topic: str, content: str, mode: str = "overwrite") -> str:
        if mode == "overwrite":
            old_entries = self._topic_entries(topic)
            for entry in old_entries:
                memory_id = entry.get("id")
                if memory_id:
                    self.client.delete(memory_id)

        self.client.add(
            messages=content,
            user_id=self.user_id,
            metadata={"topic": topic, "source": "knowledge_tool"},
            infer=bool(self.infer),
        )
        return f"✅ Knowledge '{topic}' saved ({mode})."

    def list_topics(self) -> str:
        payload = self.client.get_all(user_id=self.user_id, limit=self.max_memories)
        entries = self._extract_results(payload)
        by_topic: Dict[str, List[str]] = {}
        for entry in entries:
            metadata = self._extract_metadata(entry)
            topic = str(metadata.get("topic") or "").strip()
            if not topic:
                continue
            by_topic.setdefault(topic, []).append(self._extract_memory_text(entry))

        if not by_topic:
            return "Knowledge base is empty. Use knowledge_write to add topics."

        lines = ["# Knowledge Base Index", ""]
        for topic in sorted(by_topic):
            merged = "\n".join([t for t in by_topic[topic] if t and t.strip()])
            summary = self._extract_summary(merged)
            lines.append(f"- **{topic}**: {summary}")
        lines.append("")
        return "\n".join(lines)


def _create_mem0_client(
    *,
    drive_root: Path,
    google_api_key: str,
    qdrant_url: str,
    collection_name: str,
    embed_model: str,
    llm_model: str,
) -> Any:
    from mem0 import Memory as Mem0Memory

    parsed = urlparse(str(qdrant_url or "").strip())
    host = parsed.hostname
    port = parsed.port or 6333
    vector_cfg: Dict[str, Any] = {
        "collection_name": collection_name,
        "embedding_model_dims": 768,
        "on_disk": True,
    }
    if host:
        vector_cfg["host"] = host
        vector_cfg["port"] = port
    else:
        # Local path fallback for malformed/empty URL
        vector_cfg["path"] = str((drive_root / "memory" / "mem0_qdrant").resolve())

    history_db_path = str((drive_root / "memory" / "mem0_history.db").resolve())
    config = {
        "version": "v1.1",
        "history_db_path": history_db_path,
        "vector_store": {
            "provider": "qdrant",
            "config": vector_cfg,
        },
        "embedder": {
            "provider": "gemini",
            "config": {
                "api_key": google_api_key,
                "model": embed_model,
                "embedding_dims": 768,
            },
        },
        "llm": {
            "provider": "gemini",
            "config": {
                "api_key": google_api_key,
                "model": llm_model,
                "temperature": 0.1,
                "max_tokens": 2000,
                "top_p": 0.1,
            },
        },
    }
    return Mem0Memory.from_config(config)


def select_knowledge_backend(ctx: ToolContext) -> Tuple[KnowledgeBackend, Optional[str]]:
    backend_name = str(os.environ.get("OUROBOROS_KNOWLEDGE_BACKEND", "mem0")).strip().lower()
    mem0_enabled = _env_bool("OUROBOROS_MEM0_ENABLED", True)
    fallback_backend = FileKnowledgeBackend(ctx)

    if backend_name != "mem0":
        return fallback_backend, None

    if not mem0_enabled:
        return fallback_backend, _build_fallback_warning("disabled by OUROBOROS_MEM0_ENABLED=false")

    google_api_key = str(os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not google_api_key:
        return fallback_backend, _build_fallback_warning("missing GOOGLE_API_KEY")

    user_id = str(os.environ.get("OUROBOROS_MEM0_USER_ID", "ouroboros-agent")).strip() or "ouroboros-agent"
    infer = _env_bool("OUROBOROS_MEM0_INFER", True)
    max_memories = _env_int("OUROBOROS_MEM0_MAX_MEMORIES", 500, min_value=10, max_value=10_000)
    qdrant_url = str(os.environ.get("OUROBOROS_MEM0_QDRANT_URL", "http://qdrant:6333")).strip()
    collection_name = str(os.environ.get("OUROBOROS_MEM0_QDRANT_COLLECTION", "mem0")).strip() or "mem0"
    embed_model = str(os.environ.get("OUROBOROS_MEM0_EMBED_MODEL", "models/text-embedding-004")).strip()
    llm_model = str(os.environ.get("OUROBOROS_MEM0_LLM_MODEL", "gemini-2.5-flash")).strip()

    try:
        client = _create_mem0_client(
            drive_root=ctx.drive_root,
            google_api_key=google_api_key,
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            embed_model=embed_model,
            llm_model=llm_model,
        )
        return Mem0KnowledgeBackend(client=client, user_id=user_id, infer=infer, max_memories=max_memories), None
    except Exception as exc:
        log.warning("Mem0 init failed; using file backend fallback", exc_info=True)
        return fallback_backend, _build_fallback_warning(exc.__class__.__name__)
