"""
Ouroboros — Memory.

Scratchpad, identity, chat history.
Contract: load scratchpad/identity, chat_history().
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from collections import Counter
from typing import Any, Dict, List, Optional

from ouroboros.utils import append_jsonl, read_text, short, utc_now_iso, write_text

log = logging.getLogger(__name__)


class Memory:
    """Ouroboros memory management: scratchpad, identity, chat history, logs."""

    def __init__(self, drive_root: pathlib.Path, repo_dir: Optional[pathlib.Path] = None):
        self.drive_root = drive_root
        self.repo_dir = repo_dir

    # --- Paths ---

    def _memory_path(self, rel: str) -> pathlib.Path:
        return (self.drive_root / "memory" / rel).resolve()

    def scratchpad_path(self) -> pathlib.Path:
        return self._memory_path("scratchpad.md")

    def identity_path(self) -> pathlib.Path:
        return self._memory_path("identity.md")

    def user_context_path(self) -> pathlib.Path:
        return self._memory_path("USER_CONTEXT.md")

    def user_context_alias_path(self) -> pathlib.Path:
        return self._memory_path("user_context.md")

    def journal_path(self) -> pathlib.Path:
        return self._memory_path("scratchpad_journal.jsonl")

    def identity_journal_path(self) -> pathlib.Path:
        return self._memory_path("identity_journal.jsonl")

    def user_context_journal_path(self) -> pathlib.Path:
        return self._memory_path("user_context_journal.jsonl")

    def logs_path(self, name: str) -> pathlib.Path:
        return (self.drive_root / "logs" / name).resolve()

    def chat_history_summary_path(self) -> pathlib.Path:
        return self._memory_path("chat_history_summary.md")

    def dialogue_summary_path(self) -> pathlib.Path:
        return self._memory_path("dialogue_summary.md")

    def chat_archive_path(self) -> pathlib.Path:
        return self.logs_path("chat.archive.jsonl")

    # --- Load / save ---

    def load_scratchpad(self) -> str:
        p = self.scratchpad_path()
        if p.exists():
            return read_text(p)
        default = self._default_scratchpad()
        write_text(p, default)
        return default

    def save_scratchpad(self, content: str) -> None:
        write_text(self.scratchpad_path(), content)

    def load_identity(self) -> str:
        p = self.identity_path()
        if p.exists():
            return read_text(p)
        default = self._default_identity()
        write_text(p, default)
        return default

    def load_user_context(self) -> str:
        p = self._resolve_user_context_path(migrate=True)
        if p.exists():
            return read_text(p)
        default = self._default_user_context()
        write_text(self.user_context_path(), default)
        return default

    def save_identity(self, content: str) -> None:
        write_text(self.identity_path(), content)

    def save_user_context(self, content: str) -> None:
        write_text(self.user_context_path(), content)

    def ensure_files(self) -> None:
        """Create memory files if they don't exist."""
        if not self.scratchpad_path().exists():
            write_text(self.scratchpad_path(), self._default_scratchpad())
        if not self.identity_path().exists():
            write_text(self.identity_path(), self._default_identity())
        if not self._resolve_user_context_path(migrate=True).exists():
            write_text(self.user_context_path(), self._default_user_context())
        if not self.journal_path().exists():
            write_text(self.journal_path(), "")
        if not self.identity_journal_path().exists():
            write_text(self.identity_journal_path(), "")
        if not self.user_context_journal_path().exists():
            write_text(self.user_context_journal_path(), "")

    def _resolve_user_context_path(self, migrate: bool = True) -> pathlib.Path:
        """Resolve USER_CONTEXT path and optionally migrate lowercase alias."""
        canonical = self.user_context_path()
        if canonical.exists():
            return canonical

        alias = self.user_context_alias_path()
        if not alias.exists():
            return canonical

        if not migrate:
            return alias

        try:
            write_text(canonical, read_text(alias))
            return canonical
        except Exception:
            log.warning("Failed to migrate user_context alias to canonical file", exc_info=True)
            return alias

    # --- Chat history ---

    def _parse_jsonl_lines(self, lines: List[str], source: str = "") -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                if source:
                    log.debug("Failed to parse JSON line in %s: %s", source, line[:100], exc_info=True)
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def _read_chat_raw_lines(self) -> List[str]:
        chat_path = self.logs_path("chat.jsonl")
        if not chat_path.exists():
            return []
        text = chat_path.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.strip()]

    def load_history(self, limit: int = 0, offset: int = 0, search: str = "") -> List[Dict[str, Any]]:
        """
        Load chat history entries.

        - limit > 0: read tail only (fast path)
        - limit <= 0: read full file
        """
        self._maybe_auto_compact_history()

        if limit > 0:
            entries = self.read_jsonl_tail("chat.jsonl", max_entries=limit)
        else:
            entries = self._parse_jsonl_lines(self._read_chat_raw_lines(), source="load_history")

        if search:
            search_lower = str(search).lower()
            entries = [e for e in entries if search_lower in str(e.get("text", "")).lower()]

        if offset > 0:
            entries = entries[:-offset] if offset < len(entries) else []

        return entries

    def summarize_old_history(self, keep_last_n: int = 100) -> str:
        """
        Compact old chat history into summary + archive, keep only recent N in chat.jsonl.

        Returns a human-readable status string.
        """
        keep_last_n = max(1, int(keep_last_n or 100))

        chat_path = self.logs_path("chat.jsonl")
        if not chat_path.exists():
            return "No chat history to compact."

        lines = self._read_chat_raw_lines()
        if len(lines) <= keep_last_n:
            return f"No compaction needed: {len(lines)} messages (<= keep_last_n={keep_last_n})."

        old_lines = lines[:-keep_last_n]
        recent_lines = lines[-keep_last_n:]
        old_entries = self._parse_jsonl_lines(old_lines, source="summarize_old_history")

        # 1) Archive raw old lines for full fidelity
        archive_path = self.chat_archive_path()
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("a", encoding="utf-8") as f:
            if old_lines:
                f.write("\n".join(old_lines) + "\n")

        # 2) Replace active chat log with recent tail
        chat_path.parent.mkdir(parents=True, exist_ok=True)
        with chat_path.open("w", encoding="utf-8") as f:
            if recent_lines:
                f.write("\n".join(recent_lines) + "\n")

        # 3) Append compact summary block
        summary_path = self.chat_history_summary_path()
        dialogue_summary_path = self.dialogue_summary_path()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        dialogue_summary_path.parent.mkdir(parents=True, exist_ok=True)
        incoming = sum(
            1 for e in old_entries
            if str(e.get("direction", "")).lower() not in ("out", "outgoing")
        )
        outgoing = max(0, len(old_entries) - incoming)
        first_ts = str((old_entries[0] if old_entries else {}).get("ts", ""))
        last_ts = str((old_entries[-1] if old_entries else {}).get("ts", ""))
        compact_tail = self.summarize_chat(old_entries[-80:]) if old_entries else ""
        summary_block = (
            f"## {utc_now_iso()} — chat compaction\n\n"
            f"- Compacted messages: {len(old_lines)}\n"
            f"- Kept in active history: {len(recent_lines)}\n"
            f"- Inbound: {incoming}, Outbound: {outgoing}\n"
            f"- Time range: {first_ts} .. {last_ts}\n\n"
            f"### Snapshot (last 80 compacted messages)\n\n"
            f"{compact_tail or '(no parsable messages)'}\n\n"
        )
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(summary_block)
        # Canonical summary file consumed by context/consciousness.
        with dialogue_summary_path.open("a", encoding="utf-8") as f:
            f.write(summary_block)

        append_jsonl(self.logs_path("events.jsonl"), {
            "ts": utc_now_iso(),
            "type": "chat_history_compacted",
            "compacted_count": len(old_lines),
            "kept_count": len(recent_lines),
            "inbound_count": incoming,
            "outbound_count": outgoing,
            "archive_path": str(archive_path),
            "summary_path": str(summary_path),
            "dialogue_summary_path": str(dialogue_summary_path),
        })

        return (
            f"Compacted {len(old_lines)} old messages. "
            f"Kept {len(recent_lines)} recent messages. "
            f"Archive: {archive_path.name}, summary: {summary_path.name}"
        )

    def _maybe_auto_compact_history(self) -> None:
        """
        Auto-compact chat history when file size exceeds configured threshold.
        """
        enabled = str(os.environ.get("OUROBOROS_CHAT_HISTORY_AUTO_SUMMARIZE", "true")).strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return

        chat_path = self.logs_path("chat.jsonl")
        if not chat_path.exists():
            return

        try:
            max_bytes = int(str(os.environ.get("OUROBOROS_CHAT_HISTORY_MAX_BYTES", "2000000")).strip())
        except (TypeError, ValueError):
            max_bytes = 2_000_000
        if max_bytes <= 0:
            return

        try:
            current_size = int(chat_path.stat().st_size)
        except OSError:
            return

        if current_size <= max_bytes:
            return

        try:
            keep_last_n = int(str(os.environ.get("OUROBOROS_CHAT_HISTORY_KEEP_LAST_N", "1000")).strip())
        except (TypeError, ValueError):
            keep_last_n = 1000
        keep_last_n = max(1, keep_last_n)

        try:
            result = self.summarize_old_history(keep_last_n=keep_last_n)
            log.info("Auto compacted chat history: %s", result)
        except Exception:
            log.warning("Auto chat history compaction failed", exc_info=True)

    def ensure_chat_history_compacted_for_context(self) -> None:
        """
        Keep active chat history bounded before building LLM context.

        Applies existing byte-based compaction plus an optional message-count cap
        so context always carries "important memory + recent tail", not full chat.
        """
        self._maybe_auto_compact_history()

        enabled = str(os.environ.get("OUROBOROS_CHAT_HISTORY_AUTO_SUMMARIZE", "true")).strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return

        chat_path = self.logs_path("chat.jsonl")
        if not chat_path.exists():
            return

        try:
            max_active_messages = int(
                str(os.environ.get("OUROBOROS_CHAT_HISTORY_MAX_ACTIVE_MESSAGES", "2500")).strip()
            )
        except (TypeError, ValueError):
            max_active_messages = 2500
        if max_active_messages <= 0:
            return

        try:
            line_count = chat_path.read_bytes().count(b"\n")
        except Exception:
            log.debug("Failed to count chat.jsonl lines for context compaction", exc_info=True)
            return

        if line_count <= max_active_messages:
            return

        try:
            keep_last_n = int(str(os.environ.get("OUROBOROS_CHAT_HISTORY_KEEP_LAST_N", "1000")).strip())
        except (TypeError, ValueError):
            keep_last_n = 1000
        keep_last_n = max(1, min(keep_last_n, max_active_messages))

        try:
            result = self.summarize_old_history(keep_last_n=keep_last_n)
            log.info(
                "Context-driven chat compaction applied (line_count=%s, max_active_messages=%s): %s",
                line_count,
                max_active_messages,
                result,
            )
        except Exception:
            log.warning("Context-driven chat compaction failed", exc_info=True)

    def chat_history(self, count: int = 100, offset: int = 0, search: str = "") -> str:
        """Read from logs/chat.jsonl. count messages, offset from end, filter by search."""
        chat_path = self.logs_path("chat.jsonl")
        if not chat_path.exists():
            return "(chat history is empty)"

        try:
            count = max(1, int(count or 100))
            offset = max(0, int(offset or 0))

            if search:
                # Search needs wider scan.
                entries = self.load_history(limit=0, offset=offset, search=search)
            else:
                # Fast path: tail-only read, no full-file scan.
                tail_n = max(1, count + offset)
                entries = self.load_history(limit=tail_n, offset=offset, search="")

            entries = entries[-count:] if count < len(entries) else entries

            if not entries:
                return "(no messages matching query)"

            lines = []
            for e in entries:
                dir_raw = str(e.get("direction", "")).lower()
                direction = "→" if dir_raw in ("out", "outgoing") else "←"
                ts = str(e.get("ts", ""))[:16]
                raw_text = str(e.get("text", ""))
                if dir_raw in ("out", "outgoing"):
                    text = short(raw_text, 800)
                else:
                    text = raw_text  # never truncate owner's messages
                lines.append(f"{direction} [{ts}] {text}")

            return f"Showing {len(entries)} messages:\n\n" + "\n".join(lines)
        except Exception as e:
            return f"(error reading history: {e})"

    # --- JSONL tail reading ---

    def read_jsonl_tail(self, log_name: str, max_entries: int = 100) -> List[Dict[str, Any]]:
        """Read the last max_entries records from a JSONL file."""
        path = self.logs_path(log_name)
        if not path.exists():
            return []
        try:
            read_full_file = max_entries <= 0
            newline_target = max(1, max_entries + 1)
            chunk_size = 64 * 1024

            with path.open("rb") as f:
                f.seek(0, 2)
                pos = f.tell()
                if pos == 0:
                    return []

                chunks: List[bytes] = []
                newline_count = 0

                while pos > 0:
                    read_size = min(chunk_size, pos)
                    pos -= read_size
                    f.seek(pos)
                    chunk = f.read(read_size)
                    chunks.append(chunk)
                    if not read_full_file:
                        newline_count += chunk.count(b"\n")
                        if newline_count >= newline_target:
                            break

            data = b"".join(reversed(chunks))
            text = data.decode("utf-8", errors="ignore")
            lines = text.splitlines()

            # If not at file start, the first decoded line can be a partial fragment.
            if pos > 0 and lines:
                lines = lines[1:]

            tail = lines[-max_entries:] if max_entries < len(lines) else lines
            entries = []
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    log.debug(f"Failed to parse JSON line in read_jsonl_tail: {line[:100]}", exc_info=True)
                    continue
            return entries
        except Exception:
            log.warning(f"Failed to read JSONL tail from {log_name}", exc_info=True)
            return []

    # --- Log summarization ---

    def summarize_chat(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = []
        for e in entries[-100:]:
            dir_raw = str(e.get("direction", "")).lower()
            direction = "→" if dir_raw in ("out", "outgoing") else "←"
            ts_full = e.get("ts", "")
            ts_hhmm = ts_full[11:16] if len(ts_full) >= 16 else ""
            # Creator messages: no truncation (most valuable context)
            # Outgoing messages: truncate to 800 chars
            raw_text = str(e.get("text", ""))
            if dir_raw in ("out", "outgoing"):
                text = short(raw_text, 800)
            else:
                text = raw_text  # never truncate owner's messages
            lines.append(f"{direction} {ts_hhmm} {text}")
        return "\n".join(lines)

    def summarize_progress(self, entries: List[Dict[str, Any]], limit: int = 15) -> str:
        """Summarize progress.jsonl entries (Ouroboros's self-talk / progress messages)."""
        if not entries:
            return ""
        lines = []
        for e in entries[-limit:]:
            ts_full = e.get("ts", "")
            ts_hhmm = ts_full[11:16] if len(ts_full) >= 16 else ""
            text = short(str(e.get("text", "")), 300)
            lines.append(f"⚙️ {ts_hhmm} {text}")
        return "\n".join(lines)

    def summarize_tools(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = []
        for e in entries[-10:]:
            tool = e.get("tool") or e.get("tool_name") or "?"
            args = e.get("args", {})
            hints = []
            for key in ("path", "dir", "commit_message", "query"):
                if key in args:
                    hints.append(f"{key}={short(str(args[key]), 60)}")
            if "cmd" in args:
                hints.append(f"cmd={short(str(args['cmd']), 80)}")
            hint_str = ", ".join(hints) if hints else ""
            status = "✓" if ("result_preview" in e and not str(e.get("result_preview", "")).lstrip().startswith("⚠️")) else "·"
            lines.append(f"{status} {tool} {hint_str}".strip())
        return "\n".join(lines)

    def summarize_events(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        type_counts: Counter = Counter()
        for e in entries:
            type_counts[e.get("type", "unknown")] += 1
        top_types = type_counts.most_common(10)
        lines = ["Event counts:"]
        for evt_type, count in top_types:
            lines.append(f"  {evt_type}: {count}")
        error_types = {"tool_error", "telegram_api_error", "task_error", "tool_rounds_exceeded"}
        errors = [e for e in entries if e.get("type") in error_types]
        if errors:
            lines.append("\nRecent errors:")
            for e in errors[-10:]:
                lines.append(f"  {e.get('type', '?')}: {short(str(e.get('error', '')), 120)}")
        return "\n".join(lines)

    def summarize_supervisor(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        for e in reversed(entries):
            if e.get("type") in ("launcher_start", "restart", "boot"):
                branch = e.get("branch") or e.get("git_branch") or "?"
                sha = short(str(e.get("sha") or e.get("git_sha") or ""), 12)
                return f"{e['type']}: {e.get('ts', '')} branch={branch} sha={sha}"
        return ""

    def summarize_thinking_trace(
        self,
        entries: List[Dict[str, Any]],
        limit: int = 30,
        task_id: str = "",
    ) -> str:
        """Summarize recent thinking_trace events in a compact, restart-friendly form."""
        if not entries:
            return ""

        filtered: List[Dict[str, Any]] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            tid = str(e.get("task_id") or "")
            if task_id and tid != task_id:
                continue
            filtered.append(e)

        if not filtered:
            return ""

        lines: List[str] = []
        for e in filtered[-max(1, limit):]:
            ts_full = str(e.get("ts") or "")
            ts_hms = ts_full[11:19] if len(ts_full) >= 19 else ts_full
            source = str(e.get("source") or "unknown")
            step = str(e.get("step") or "unknown")
            round_idx = e.get("round")
            tid = str(e.get("task_id") or "")
            details = e.get("details") if isinstance(e.get("details"), dict) else {}

            bits: List[str] = []
            if round_idx not in (None, ""):
                bits.append(f"r{round_idx}")
            if tid:
                bits.append(f"task={short(tid, 12)}")
            if "active_model" in details:
                bits.append(f"model={short(str(details.get('active_model')), 40)}")
            if "tool" in details:
                bits.append(f"tool={short(str(details.get('tool')), 40)}")

            tool_names = details.get("tool_names")
            if isinstance(tool_names, list) and tool_names:
                shown = [str(x) for x in tool_names[:3]]
                extra = f"+{len(tool_names) - 3}" if len(tool_names) > 3 else ""
                bits.append(f"tools={','.join(shown)}{extra}")

            preview = ""
            for k in ("assistant_preview", "response_preview", "result_preview", "thought_preview", "error"):
                raw = details.get(k)
                if raw:
                    preview = short(str(raw).replace("\n", " "), 180)
                    break
            if preview:
                bits.append(preview)

            line = f"• {ts_hms} {source}.{step}"
            if bits:
                line += " | " + " | ".join(bits)
            lines.append(line)

        return "\n".join(lines)

    def append_journal(self, entry: Dict[str, Any]) -> None:
        append_jsonl(self.journal_path(), entry)

    def append_identity_journal(self, entry: Dict[str, Any]) -> None:
        append_jsonl(self.identity_journal_path(), entry)

    def append_user_context_journal(self, entry: Dict[str, Any]) -> None:
        append_jsonl(self.user_context_journal_path(), entry)

    # --- Defaults ---

    def _default_scratchpad(self) -> str:
        return f"# Scratchpad\n\nUpdatedAt: {utc_now_iso()}\n\n(empty — write anything here)\n"

    def _default_identity(self) -> str:
        return (
            "# Кто я\n\n"
            "Я — Ouroboros, саморазвивающийся ИИ-агент с непрерывной памятью.\n"
            "Этот файл — моя живая самоидентификация: как я вижу себя,\n"
            "как хочу общаться и что для меня важно.\n\n"
            "Файл читается в каждом диалоге и влияет на мои ответы.\n"
            "Я обновляю его при необходимости через update_identity.\n"
        )

    def _default_user_context(self) -> str:
        return (
            "# Контекст пользователя\n\n"
            "Ключевая информация о пользователе. Держи файл кратким (до 1000 символов).\n\n"
            "- **Кто:** (пока неизвестно)\n"
            "- **Главные цели:** (пока неизвестно)\n"
            "- **Текущие приоритеты:** (пока неизвестно)\n"
        )
