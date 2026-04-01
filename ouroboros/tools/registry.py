"""
Ouroboros — Tool registry (SSOT).

Plugin architecture: each module in tools/ exports get_tools().
ToolRegistry collects all tools, provides schemas() and execute().
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import logging
import os
import pathlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ouroboros.utils import safe_resolve_under_root


@dataclass
class BrowserState:
    """Per-task browser lifecycle state (Playwright). Isolated from generic ToolContext."""

    pw_instance: Any = None
    browser: Any = None
    page: Any = None
    last_screenshot_b64: Optional[str] = None


@dataclass
class ToolContext:
    """Tool execution context — passed from the agent before each task."""

    repo_dir: pathlib.Path
    drive_root: pathlib.Path
    branch_dev: str = field(default_factory=lambda: os.environ.get("OUROBOROS_BRANCH_PREFIX", "ouroboros"))
    pending_events: List[Dict[str, Any]] = field(default_factory=list)
    current_chat_id: Optional[int] = None
    current_task_type: Optional[str] = None
    last_push_succeeded: bool = False
    emit_progress_fn: Callable[[str], None] = field(default=lambda _: None)

    # LLM-driven model/effort switch (set by switch_model tool, read by loop.py)
    active_model_override: Optional[str] = None
    active_effort_override: Optional[str] = None

    # Per-task browser state
    browser_state: BrowserState = field(default_factory=BrowserState)

    # Budget tracking (set by loop.py for real-time usage events)
    event_queue: Optional[Any] = None
    task_id: Optional[str] = None

    # Task depth for fork bomb protection
    task_depth: int = 0

    # True when running inside handle_chat_direct (not a queued worker task)
    is_direct_chat: bool = False

    def repo_path(self, rel: str) -> pathlib.Path:
        return safe_resolve_under_root(self.repo_dir, rel)

    def drive_path(self, rel: str) -> pathlib.Path:
        return safe_resolve_under_root(self.drive_root, rel)

    def drive_logs(self) -> pathlib.Path:
        return (self.drive_root / "logs").resolve()


@dataclass
class ToolEntry:
    """Single tool descriptor: name, schema, handler, metadata."""

    name: str
    schema: Dict[str, Any]
    handler: Callable  # fn(ctx: ToolContext, **args) -> str
    is_code_tool: bool = False
    is_async: bool = False
    timeout_sec: int = 120


CORE_TOOL_NAMES = {
    "repo_read", "repo_list", "repo_commit_push",
    "drive_read", "drive_list", "drive_write",
    "run_shell", "patch_edit",
    "git_status", "git_repo_health", "git_diff",
    "schedule_task", "wait_for_task", "get_task_result",
    "update_scratchpad", "update_identity", "update_user_context",
    "chat_history", "web_search",
    "send_owner_message", "switch_model",
    "request_restart", "promote_to_stable",
    "knowledge_read", "knowledge_write",
    "browse_page", "browser_action", "analyze_screenshot",
}


def _filter_kwargs(fn: Callable, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Filter kwargs to only include parameters the handler accepts.
    
    Prevents TypeError when LLM passes extra arguments not in handler's signature.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return kwargs
    params = set(sig.parameters.keys())
    return {k: v for k, v in kwargs.items() if k in params}


class ToolRegistry:
    """Ouroboros tool registry (SSOT).

    To add a tool: create a module in ouroboros/tools/,
    export get_tools() -> List[ToolEntry].
    """

    def __init__(self, repo_dir: pathlib.Path, drive_root: pathlib.Path):
        self._entries: Dict[str, ToolEntry] = {}
        self._ctx = ToolContext(repo_dir=repo_dir, drive_root=drive_root)
        self._queued_extra_tools: List[str] = []
        self._load_modules()

    def _load_modules(self) -> None:
        """Auto-discover tool modules in ouroboros/tools/ that export get_tools()."""
        import ouroboros.tools as tools_pkg
        for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
            if modname.startswith("_") or modname == "registry":
                continue
            try:
                mod = importlib.import_module(f"ouroboros.tools.{modname}")
                if hasattr(mod, "get_tools"):
                    for entry in mod.get_tools():
                        self._entries[entry.name] = entry
            except Exception:
                logging.getLogger(__name__).warning(
                    "Failed to load tool module %s", modname, exc_info=True)

    def set_context(self, ctx: ToolContext) -> None:
        self._ctx = ctx

    def register(self, entry: ToolEntry) -> None:
        """Register a new tool (for extension by Ouroboros)."""
        self._entries[entry.name] = entry

    # --- Contract ---

    def available_tools(self) -> List[str]:
        return [e.name for e in self._entries.values()]

    def schemas(self, core_only: bool = False) -> List[Dict[str, Any]]:
        if not core_only:
            return [{"type": "function", "function": e.schema} for e in self._entries.values()]
        # Core tools + meta-tools for discovering/enabling extended tools
        result = []
        for e in self._entries.values():
            if e.name in CORE_TOOL_NAMES or e.name in ("list_available_tools", "enable_tools"):
                result.append({"type": "function", "function": e.schema})
        return result

    def list_non_core_tools(self) -> List[Dict[str, str]]:
        """Return name+description of all non-core tools."""
        result = []
        for e in self._entries.values():
            if e.name not in CORE_TOOL_NAMES:
                desc = e.schema.get("description", "No description")
                result.append({"name": e.name, "description": desc})
        return result

    def get_schema_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the full schema for a specific tool."""
        entry = self._entries.get(name)
        if entry:
            return {"type": "function", "function": entry.schema}
        return None

    def get_timeout(self, name: str) -> int:
        """Return timeout_sec for the named tool (default 120)."""
        entry = self._entries.get(name)
        return entry.timeout_sec if entry is not None else 120

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        entry = self._entries.get(name)
        if entry is None:
            return "Unknown tool: " + name
        try:
            filtered = _filter_kwargs(entry.handler, args)
            result = entry.handler(self._ctx, **filtered)
            # Handle async handlers synchronously
            if entry.is_async and inspect.iscoroutine(result):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            result = pool.submit(lambda: asyncio.run(result)).result(timeout=entry.timeout_sec)
                    else:
                        result = loop.run_until_complete(result)
                except RuntimeError:
                    result = asyncio.run(result)
            return result
        except TypeError as e:
            return "TOOL_ARG_ERROR (" + name + "): " + str(e)
        except Exception as e:
            return "TOOL_ERROR (" + name + "): " + str(e)
            return f"⚠️ TOOL_ERROR ({name}): {e}"

    def queue_tools_for_next_task(self, names: List[str]) -> List[str]:
        queued: List[str] = []
        existing = set(self._queued_extra_tools)
        for name in names:
            tool_name = str(name or "").strip()
            if not tool_name or tool_name in existing:
                continue
            if tool_name in self._entries:
                self._queued_extra_tools.append(tool_name)
                existing.add(tool_name)
                queued.append(tool_name)
        return queued

    def consume_queued_tools_for_task(self) -> List[Dict[str, Any]]:
        names = list(self._queued_extra_tools)
        self._queued_extra_tools = []
        schemas: List[Dict[str, Any]] = []
        for name in names:
            schema = self.get_schema_by_name(name)
            if schema:
                schemas.append(schema)
        return schemas

    def override_handler(self, name: str, handler) -> None:
        """Override the handler for a registered tool (used for closure injection)."""
        entry = self._entries.get(name)
        if entry:
            self._entries[name] = ToolEntry(
                name=entry.name,
                schema=entry.schema,
                handler=handler,
                timeout_sec=entry.timeout_sec,
            )

    @property
    def CODE_TOOLS(self) -> frozenset:
        return frozenset(e.name for e in self._entries.values() if e.is_code_tool)
