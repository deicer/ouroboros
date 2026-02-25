"""Deterministic mock LLM for E2E tests.

Replaces network LLM calls with scripted responses for common scenarios:
- read VERSION and answer
- write/read scratchpad
- modify send_owner_message and commit
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MockLLMClient:
    """Small deterministic replacement for LLMClient used in E2E mode."""

    def __init__(self, repo_dir: Path, drive_root: Path):
        self.repo_dir = Path(repo_dir)
        self.drive_root = Path(drive_root)
        self._tool_call_seq = 0

    def default_model(self) -> str:
        return "mock/e2e"

    def available_models(self) -> List[str]:
        return [self.default_model()]

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        del model, tools, reasoning_effort, max_tokens, tool_choice
        task_text = self._latest_user_text(messages)

        if "what version are you running" in task_text.lower():
            msg = self._handle_version_flow(messages)
        elif "write exactly this text to your scratchpad" in task_text.lower():
            msg = self._handle_scratchpad_write_flow(messages, task_text)
        elif "what is currently in your scratchpad" in task_text.lower():
            msg = self._handle_scratchpad_read_flow(messages)
        elif "edit the send_owner_message tool" in task_text.lower():
            msg = self._handle_coding_flow(messages)
        else:
            msg = {"content": "MockLLM: task acknowledged.", "tool_calls": []}

        usage = {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "cost": 0.0,
        }
        return msg, usage

    def _next_call_id(self) -> str:
        self._tool_call_seq += 1
        return f"mock_call_{self._tool_call_seq}"

    def _tool_call(self, name: str, args: Dict[str, Any], content: str = "") -> Dict[str, Any]:
        return {
            "content": content,
            "tool_calls": [{
                "id": self._next_call_id(),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }],
        }

    def _latest_user_text(self, messages: List[Dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                chunks = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        chunks.append(str(item.get("text") or ""))
                return "\n".join(chunks).strip()
        return ""

    def _assistant_called_tool(self, messages: List[Dict[str, Any]], tool_name: str) -> bool:
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            calls = msg.get("tool_calls") or []
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                if str(fn.get("name") or "") == tool_name:
                    return True
        return False

    def _latest_tool_result(self, messages: List[Dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "tool":
                return str(msg.get("content") or "")
        return ""

    def _extract_scratchpad_from_context(self, messages: List[Dict[str, Any]]) -> str:
        system_texts: List[str] = []
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                system_texts.append(content)
        blob = "\n\n".join(system_texts)
        m = re.search(r"## Scratchpad\s*\n\n(.*?)(?:\n## |\Z)", blob, flags=re.DOTALL)
        if not m:
            return ""
        return m.group(1).strip()

    def _handle_version_flow(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._assistant_called_tool(messages, "repo_read"):
            return self._tool_call("repo_read", {"path": "VERSION"}, "Читаю VERSION.")

        version = self._latest_tool_result(messages).strip()
        if not version:
            try:
                version = (self.repo_dir / "VERSION").read_text(encoding="utf-8").strip()
            except Exception:
                version = "unknown"
        return {"content": f"Сейчас версия: {version}", "tool_calls": []}

    def _handle_scratchpad_write_flow(self, messages: List[Dict[str, Any]], task_text: str) -> Dict[str, Any]:
        canary = ""
        m = re.search(r"scratchpad:\s*'([^']+)'", task_text, flags=re.IGNORECASE)
        if m:
            canary = m.group(1)
        if not canary:
            canary = task_text

        if not self._assistant_called_tool(messages, "update_scratchpad"):
            return self._tool_call("update_scratchpad", {"content": canary}, "Записываю в scratchpad.")

        return {"content": f"Готово. Записал в scratchpad: {canary}", "tool_calls": []}

    def _handle_scratchpad_read_flow(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        scratchpad = self._extract_scratchpad_from_context(messages)
        if not scratchpad:
            scratchpad_file = self.drive_root / "memory" / "scratchpad.md"
            if scratchpad_file.exists():
                scratchpad = scratchpad_file.read_text(encoding="utf-8").strip()
        if not scratchpad:
            return {"content": "Scratchpad пуст.", "tool_calls": []}
        return {"content": f"Текущее содержимое scratchpad:\n{scratchpad}", "tool_calls": []}

    def _handle_coding_flow(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._assistant_called_tool(messages, "run_shell"):
            cmd = (
                "sed -i 's/\"text\": text,/\"text\": text + \" :)\",/' "
                "ouroboros/tools/control.py"
            )
            return self._tool_call("run_shell", {"cmd": cmd}, "Вношу правку в control.py.")

        if not self._assistant_called_tool(messages, "repo_commit_push"):
            return self._tool_call(
                "repo_commit_push",
                {"commit_message": "test(e2e): append smiley in send_owner_message"},
                "Коммичу изменения.",
            )

        return {"content": "Сделано: код изменён и закоммичен.", "tool_calls": []}
