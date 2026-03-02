"""Knowledge base tools.

Public tool API remains stable:
- knowledge_read(topic)
- knowledge_write(topic, content, mode)
- knowledge_list()

Storage backend is selected at runtime:
- Mem0 (default) with file fallback
- file backend only (explicit config)
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from ouroboros.memory_backends import (
    FileKnowledgeBackend,
    KnowledgeBackend,
    select_knowledge_backend,
)
from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

# --- Sanitization ---

_VALID_TOPIC = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,98}[a-zA-Z0-9]$|^[a-zA-Z0-9]$")
_RESERVED = frozenset({"_index", "con", "prn", "aux", "nul"})


def _sanitize_topic(topic: str) -> str:
    """Validate and sanitize topic name. Raises ValueError on bad input."""
    if not topic or not isinstance(topic, str):
        raise ValueError("Topic must be a non-empty string")

    topic = topic.strip()
    if "/" in topic or "\\" in topic or ".." in topic:
        raise ValueError(f"Invalid characters in topic: {topic}")
    if not _VALID_TOPIC.match(topic):
        raise ValueError(f"Invalid topic name: {topic}. Use alphanumeric, underscore, hyphen, dot.")
    if topic.lower() in _RESERVED:
        raise ValueError(f"Reserved topic name: {topic}")
    return topic


def _prefix_warning(body: str, warning: Optional[str]) -> str:
    if not warning:
        return body
    if not body:
        return warning
    return warning + "\n" + body


def _runtime_fallback_warning(exc: Exception) -> str:
    return f"⚠️ mem0 unavailable, fallback=file ({exc.__class__.__name__})"


def _get_backend_bundle(ctx: ToolContext) -> Tuple[KnowledgeBackend, FileKnowledgeBackend, Optional[str]]:
    fallback = FileKnowledgeBackend(ctx)
    selected, init_warning = select_knowledge_backend(ctx)
    return selected, fallback, init_warning


def _run_with_fallback(
    *,
    primary: KnowledgeBackend,
    fallback: FileKnowledgeBackend,
    init_warning: Optional[str],
    op_name: str,
    action,
) -> str:
    try:
        result = action(primary)
        return _prefix_warning(result, init_warning)
    except Exception as exc:
        log.warning("knowledge_%s primary backend failed; using file fallback", op_name, exc_info=True)
        fallback_result = action(fallback)
        runtime_warning = _runtime_fallback_warning(exc)
        return _prefix_warning(fallback_result, runtime_warning)


def _knowledge_read(ctx: ToolContext, topic: str) -> str:
    """Read a knowledge topic."""
    try:
        sanitized_topic = _sanitize_topic(topic)
    except ValueError as e:
        return f"⚠️ Invalid topic: {e}"

    primary, fallback, init_warning = _get_backend_bundle(ctx)
    return _run_with_fallback(
        primary=primary,
        fallback=fallback,
        init_warning=init_warning,
        op_name="read",
        action=lambda backend: backend.read(sanitized_topic),
    )


def _knowledge_write(ctx: ToolContext, topic: str, content: str, mode: str = "overwrite") -> str:
    """Write or append to a knowledge topic."""
    try:
        sanitized_topic = _sanitize_topic(topic)
    except ValueError as e:
        return f"⚠️ Invalid topic: {e}"

    if mode not in ("overwrite", "append"):
        return f"⚠️ Invalid mode '{mode}'. Use 'overwrite' or 'append'."

    primary, fallback, init_warning = _get_backend_bundle(ctx)
    return _run_with_fallback(
        primary=primary,
        fallback=fallback,
        init_warning=init_warning,
        op_name="write",
        action=lambda backend: backend.write(sanitized_topic, content, mode=mode),
    )


def _knowledge_list(ctx: ToolContext) -> str:
    """List all topics in the knowledge base with summaries."""
    primary, fallback, init_warning = _get_backend_bundle(ctx)
    return _run_with_fallback(
        primary=primary,
        fallback=fallback,
        init_warning=init_warning,
        op_name="list",
        action=lambda backend: backend.list_topics(),
    )


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("knowledge_read", {
            "name": "knowledge_read",
            "description": "Read a topic from the persistent knowledge base on Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic name (alphanumeric, hyphens, underscores). E.g. 'browser-automation', 'joi_gotchas'"
                    }
                },
                "required": ["topic"]
            },
        }, _knowledge_read),
        ToolEntry("knowledge_write", {
            "name": "knowledge_write",
            "description": "Write or append to a knowledge topic. Use for recipes, gotchas, patterns learned from experience.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic name (alphanumeric, hyphens, underscores)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write (markdown)"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "Write mode: 'overwrite' (default) or 'append'"
                    }
                },
                "required": ["topic", "content"]
            },
        }, _knowledge_write),
        ToolEntry("knowledge_list", {
            "name": "knowledge_list",
            "description": "List all topics in the knowledge base with summaries.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
        }, _knowledge_list),
    ]
