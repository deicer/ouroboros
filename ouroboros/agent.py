"""
Ouroboros agent core — thin orchestrator.

Delegates to: loop.py (LLM tool loop), tools/ (tool schemas/execution),
llm.py (LLM calls), memory.py (scratchpad/identity),
context.py (context building), review.py (code collection/metrics).
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import pathlib
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

from ouroboros.utils import (
    utc_now_iso, read_text, append_jsonl,
    truncate_for_log,
    get_git_info, sanitize_task_for_event, safe_resolve_under_root,
)
from ouroboros.llm import LLMClient, add_usage
from ouroboros.tools import ToolRegistry
from ouroboros.tools.registry import ToolContext
from ouroboros.memory import Memory
from ouroboros.context import build_llm_messages
from ouroboros.loop import run_llm_loop


# ---------------------------------------------------------------------------
# Module-level guard for one-time worker boot logging
# ---------------------------------------------------------------------------
_worker_boot_logged = False
_worker_boot_lock = threading.Lock()

_LOOP_GUARD_EN_MARKERS = (
    "stuck in repeated identical tool batches",
    "stuck in repeated tool errors",
    "обнаружен цикл одинаковых действий",
    "обнаружены повторяющиеся ошибки инструментов",
)
_LOOP_GUARD_USER_TEXT_RU = (
    "⚠️ Обнаружен цикл повторяющихся действий. Остановил текущий ход и жду короткое уточнение, чтобы продолжить без повторов."
)
_STATUS_TEMPLATE_MARKERS = (
    "## фактологичный апдейт",
    "## фоновый цикл",
    "## рефлексия",
    "## статус",
)
_FOCUS_STOPWORDS = {
    "что", "где", "когда", "почему", "как", "зачем", "или", "это", "этот", "эта", "эти",
    "уже", "еще", "ещё", "тут", "там", "мне", "тебя", "твой", "мою", "мой", "моих",
    "про", "для", "без", "вот", "давай", "можешь", "можем", "надо", "нужно", "если",
    "чтобы", "только", "сам", "сама", "сейчас", "тогда", "просто", "очень",
    "the", "and", "with", "from", "your", "about", "what", "why", "how",
}


def _split_user_and_log_text(text: str) -> Tuple[str, str]:
    """Keep rich diagnostics for logs, but send concise Russian guard text to user."""
    raw_text = str(text or "")
    low = raw_text.lower()
    if any(marker in low for marker in _LOOP_GUARD_EN_MARKERS):
        return _LOOP_GUARD_USER_TEXT_RU, raw_text
    return raw_text, raw_text


def _is_auto_resume_task(task: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(task, dict):
        return False
    task_text = str(task.get("text") or "")
    return task_text.startswith("[auto-resume after restart]")


def _strip_background_preamble_for_user(text: str, task: Optional[Dict[str, Any]]) -> str:
    """
    Remove leaked "background cycle" sections from direct user replies.

    Keep auto-resume reports unchanged because they are expected to be status-heavy.
    """
    if _is_auto_resume_task(task):
        return text
    raw = str(text or "")
    low = raw.lower()
    if "## фоновый цикл" not in low:
        return raw

    # Common pattern: leaked background report + explicit answer section.
    markers = ("## Ответ", "## ответ", "**Ответ:**", "**ответ:**")
    for marker in markers:
        idx = raw.find(marker)
        if idx >= 0:
            cleaned = raw[idx:].strip()
            if cleaned:
                return cleaned

    # No explicit answer section found — avoid leaking internal background report.
    return "⚠️ Внутренний отчёт не должен был попасть в чат. Повтори запрос, отвечу по делу."


def _is_status_template_reply(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if any(low.startswith(marker) for marker in _STATUS_TEMPLATE_MARKERS):
        return True
    return "текущее состояние" in low and ("следующий шаг" in low or "модель" in low)


def _text_similarity(a: str, b: str) -> float:
    a_norm = " ".join(str(a or "").lower().split())
    b_norm = " ".join(str(b or "").lower().split())
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def _extract_focus_tokens(text: str) -> List[str]:
    tokens = []
    for token in re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", str(text or "").lower()):
        if token in _FOCUS_STOPWORDS:
            continue
        tokens.append(token)
    # stable dedup
    out: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _response_focus_overlap_ratio(response_text: str, task_text: str) -> float:
    focus = _extract_focus_tokens(task_text)
    if not focus:
        return 1.0
    response_low = str(response_text or "").lower()
    hits = sum(1 for token in focus if token in response_low)
    return hits / float(len(focus))


# ---------------------------------------------------------------------------
# Environment + Paths
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Env:
    repo_dir: pathlib.Path
    drive_root: pathlib.Path
    branch_dev: str = field(default_factory=lambda: os.environ.get("OUROBOROS_BRANCH_PREFIX", "ouroboros"))

    def repo_path(self, rel: str) -> pathlib.Path:
        return safe_resolve_under_root(self.repo_dir, rel)

    def drive_path(self, rel: str) -> pathlib.Path:
        return safe_resolve_under_root(self.drive_root, rel)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class OuroborosAgent:
    """One agent instance per worker process. Mostly stateless; long-term state lives on Drive."""

    def __init__(self, env: Env, event_queue: Any = None):
        self.env = env
        self._pending_events: List[Dict[str, Any]] = []
        self._event_queue: Any = event_queue
        self._current_chat_id: Optional[int] = None
        self._current_task_type: Optional[str] = None

        # Message injection: owner can send messages while agent is busy
        self._incoming_messages: queue.Queue = queue.Queue()
        self._busy = False
        self._last_progress_ts: float = 0.0
        self._task_started_ts: float = 0.0

        # SSOT modules
        self.llm = LLMClient()
        self.tools = ToolRegistry(repo_dir=env.repo_dir, drive_root=env.drive_root)
        self.memory = Memory(drive_root=env.drive_root, repo_dir=env.repo_dir)

        self._log_worker_boot_once()

    def inject_message(self, text: str) -> None:
        """Thread-safe: inject owner message into the active conversation."""
        self._incoming_messages.put(text)

    def _log_worker_boot_once(self) -> None:
        global _worker_boot_logged
        try:
            with _worker_boot_lock:
                if _worker_boot_logged:
                    return
                _worker_boot_logged = True
            git_branch, git_sha = get_git_info(self.env.repo_dir)
            append_jsonl(self.env.drive_path('logs') / 'events.jsonl', {
                'ts': utc_now_iso(), 'type': 'worker_boot',
                'pid': os.getpid(), 'git_branch': git_branch, 'git_sha': git_sha,
            })
            self._verify_restart(git_sha)
            self._verify_system_state(git_sha)
        except Exception:
            log.warning("Worker boot logging failed", exc_info=True)
            return

    def _verify_restart(self, git_sha: str) -> None:
        """Best-effort restart verification."""
        try:
            pending_path = self.env.drive_path('state') / 'pending_restart_verify.json'
            claim_path = pending_path.with_name(f"pending_restart_verify.claimed.{os.getpid()}.json")
            try:
                os.rename(str(pending_path), str(claim_path))
            except (FileNotFoundError, Exception):
                return
            try:
                claim_data = json.loads(read_text(claim_path))
                expected_sha = str(claim_data.get("expected_sha", "")).strip()
                ok = bool(expected_sha and expected_sha == git_sha)
                append_jsonl(self.env.drive_path('logs') / 'events.jsonl', {
                    'ts': utc_now_iso(), 'type': 'restart_verify',
                    'pid': os.getpid(), 'ok': ok,
                    'expected_sha': expected_sha, 'observed_sha': git_sha,
                })
            except Exception:
                log.debug("Failed to log restart verify event", exc_info=True)
                pass
            try:
                claim_path.unlink()
            except Exception:
                log.debug("Failed to delete restart verify claim file", exc_info=True)
                pass
        except Exception:
            log.debug("Restart verification failed", exc_info=True)
            pass

    def _check_uncommitted_changes(self) -> Tuple[dict, int]:
        """Check for uncommitted changes and attempt auto-rescue commit & push."""
        import re
        import subprocess
        # Remove stale index.lock (race condition when multiple workers start)
        lock_path = self.env.repo_dir / ".git" / "index.lock"
        if lock_path.exists():
            try:
                import time
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > 30:  # stale if older than 30s
                    lock_path.unlink(missing_ok=True)
                    log.warning(f"Removed stale .git/index.lock (age={lock_age:.0f}s)")
                else:
                    # Another process is actively using git — skip
                    return {"status": "ok", "note": "index.lock held by another process"}, 0
            except Exception:
                pass
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.env.repo_dir),
                capture_output=True, text=True, timeout=10, check=True
            )
            dirty_files = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
            if dirty_files:
                # Auto-rescue: commit and push
                auto_committed = False
                try:
                    # Stage all changes (tracked + untracked init files)
                    subprocess.run(["git", "add", "-A"], cwd=str(self.env.repo_dir), timeout=10, check=True)
                    subprocess.run(
                        ["git", "commit", "-m", "auto-rescue: uncommitted changes detected on startup"],
                        cwd=str(self.env.repo_dir), timeout=30, check=True
                    )
                    # Validate branch name
                    if not re.match(r'^[a-zA-Z0-9_/-]+$', self.env.branch_dev):
                        raise ValueError(f"Invalid branch name: {self.env.branch_dev}")
                    # Pull with rebase before push
                    subprocess.run(
                        ["git", "pull", "--rebase", "origin", self.env.branch_dev],
                        cwd=str(self.env.repo_dir), timeout=60, check=True
                    )
                    # Push
                    try:
                        subprocess.run(
                            ["git", "push", "origin", self.env.branch_dev],
                            cwd=str(self.env.repo_dir), timeout=60, check=True
                        )
                        auto_committed = True
                        log.warning(f"Auto-rescued {len(dirty_files)} uncommitted files on startup")
                    except subprocess.CalledProcessError:
                        # If push fails, undo the commit
                        subprocess.run(
                            ["git", "reset", "HEAD~1"],
                            cwd=str(self.env.repo_dir), timeout=10, check=True
                        )
                        raise
                except Exception as e:
                    log.warning(f"Failed to auto-rescue uncommitted changes: {e}", exc_info=True)
                return {
                    "status": "warning", "files": dirty_files[:20],
                    "auto_committed": auto_committed,
                }, 1
            else:
                return {"status": "ok"}, 0
        except Exception as e:
            return {"status": "error", "error": str(e)}, 0

    def _check_version_sync(self) -> Tuple[dict, int]:
        """Check VERSION file sync with git tags and pyproject.toml."""
        import subprocess
        import re
        try:
            version_file = read_text(self.env.repo_path("VERSION")).strip()
            issue_count = 0
            result_data = {"version_file": version_file}

            # Check pyproject.toml version
            pyproject_path = self.env.repo_path("pyproject.toml")
            pyproject_content = read_text(pyproject_path)
            match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pyproject_content, re.MULTILINE)
            if match:
                pyproject_version = match.group(1)
                result_data["pyproject_version"] = pyproject_version
                if version_file != pyproject_version:
                    result_data["status"] = "warning"
                    issue_count += 1

            # Check README.md version (Bible P7: VERSION == README version)
            try:
                readme_content = read_text(self.env.repo_path("README.md"))
                readme_match = re.search(r'\*\*Version:\*\*\s*(\d+\.\d+\.\d+)', readme_content)
                if readme_match:
                    readme_version = readme_match.group(1)
                    result_data["readme_version"] = readme_version
                    if version_file != readme_version:
                        result_data["status"] = "warning"
                        issue_count += 1
            except Exception:
                log.debug("Failed to check README.md version", exc_info=True)

            # Check git tags
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=str(self.env.repo_dir),
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                result_data["status"] = "warning"
                result_data["message"] = "no_tags"
                return result_data, issue_count
            else:
                latest_tag = result.stdout.strip().lstrip('v')
                result_data["latest_tag"] = latest_tag
                if version_file != latest_tag:
                    result_data["status"] = "warning"
                    issue_count += 1

            if issue_count == 0:
                result_data["status"] = "ok"

            return result_data, issue_count
        except Exception as e:
            return {"status": "error", "error": str(e)}, 0

    def _verify_system_state(self, git_sha: str) -> None:
        """Bible Principle 1: verify system state on every startup.

        Checks:
        - Uncommitted changes (auto-rescue commit & push)
        - VERSION file sync with git tags
        """
        checks = {}
        issues = 0
        drive_logs = self.env.drive_path("logs")

        # 1. Uncommitted changes
        checks["uncommitted_changes"], issue_count = self._check_uncommitted_changes()
        issues += issue_count

        # 2. VERSION vs git tag
        checks["version_sync"], issue_count = self._check_version_sync()
        issues += issue_count

        # Log verification result
        event = {
            "ts": utc_now_iso(),
            "type": "startup_verification",
            "checks": checks,
            "issues_count": issues,
            "git_sha": git_sha,
        }
        append_jsonl(drive_logs / "events.jsonl", event)

        if issues > 0:
            log.warning(f"Startup verification found {issues} issue(s): {checks}")

    # =====================================================================
    # Main entry point
    # =====================================================================

    def _prepare_task_context(self, task: Dict[str, Any]) -> Tuple[ToolContext, List[Dict[str, Any]], Dict[str, Any]]:
        """Set up ToolContext, build messages, return (ctx, messages, cap_info)."""
        drive_logs = self.env.drive_path("logs")
        sanitized_task = sanitize_task_for_event(task, drive_logs)
        append_jsonl(drive_logs / "events.jsonl", {"ts": utc_now_iso(), "type": "task_received", "task": sanitized_task})

        # Set tool context for this task
        ctx = ToolContext(
            repo_dir=self.env.repo_dir,
            drive_root=self.env.drive_root,
            branch_dev=self.env.branch_dev,
            pending_events=self._pending_events,
            current_chat_id=self._current_chat_id,
            current_task_type=self._current_task_type,
            emit_progress_fn=self._emit_progress,
            task_depth=int(task.get("depth", 0)),
            is_direct_chat=bool(task.get("_is_direct_chat")),
        )
        self.tools.set_context(ctx)

        # Typing indicator via event queue (no direct Telegram API)
        self._emit_typing_start()

        # --- Build context (delegated to context.py) ---
        messages, cap_info = build_llm_messages(
            env=self.env,
            memory=self.memory,
            task=task,
            review_context_builder=self._build_review_context,
        )

        if cap_info.get("trimmed_sections"):
            try:
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(), "type": "context_soft_cap_trim",
                    "task_id": task.get("id"), **cap_info,
                })
            except Exception:
                log.warning("Failed to log context soft cap trim event", exc_info=True)
                pass

        return ctx, messages, cap_info

    def handle_task(self, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._busy = True
        start_time = time.time()
        self._task_started_ts = start_time
        self._last_progress_ts = start_time
        self._pending_events = []
        self._current_chat_id = int(task.get("chat_id") or 0) or None
        self._current_task_type = str(task.get("type") or "")

        drive_logs = self.env.drive_path("logs")
        heartbeat_stop = self._start_task_heartbeat_loop(str(task.get("id") or ""))

        try:
            # --- Prepare task context ---
            ctx, messages, cap_info = self._prepare_task_context(task)

            # --- LLM loop (delegated to loop.py) ---
            usage: Dict[str, Any] = {}
            llm_trace: Dict[str, Any] = {"assistant_notes": [], "tool_calls": []}

            # Set initial reasoning effort based on task type
            task_type_str = str(task.get("type") or "").lower()
            if task_type_str in ("evolution", "review"):
                initial_effort = "high"
            else:
                initial_effort = "medium"

            try:
                text, usage, llm_trace = run_llm_loop(
                    messages=messages,
                    tools=self.tools,
                    llm=self.llm,
                    drive_logs=drive_logs,
                    emit_progress=self._emit_progress,
                    incoming_messages=self._incoming_messages,
                    task_type=task_type_str,
                    task_id=str(task.get("id") or ""),
                    event_queue=self._event_queue,
                    initial_effort=initial_effort,
                    drive_root=self.env.drive_root,
                    is_direct_chat=bool(task.get("_is_direct_chat")),
                )
            except Exception as e:
                tb = traceback.format_exc()
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(), "type": "task_error",
                    "task_id": task.get("id"), "error": repr(e),
                    "traceback": truncate_for_log(tb, 2000),
                })
                text = f"⚠️ Error during processing: {type(e).__name__}: {e}"

            # Empty response guard
            if not isinstance(text, str) or not text.strip():
                text = "⚠️ Model returned an empty response. Try rephrasing your request."

            # Refresh durable file map after tool activity so new files are discoverable
            # across cycles/restarts without path guessing.
            try:
                if llm_trace.get("tool_calls"):
                    self.memory.refresh_path_catalog(
                        reason=f"task_done:{task.get('id')}",
                        max_files=20000,
                    )
            except Exception:
                log.debug("Failed to refresh path catalog at task end", exc_info=True)

            # Emit events for supervisor
            self._emit_task_results(task, text, usage, llm_trace, start_time, drive_logs)
            return list(self._pending_events)

        finally:
            self._busy = False
            # Clean up browser if it was used during this task
            try:
                from ouroboros.tools.browser import cleanup_browser
                cleanup_browser(self.tools._ctx)
            except Exception:
                log.debug("Failed to cleanup browser", exc_info=True)
                pass
            while not self._incoming_messages.empty():
                try:
                    self._incoming_messages.get_nowait()
                except queue.Empty:
                    break
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            self._current_task_type = None

    # =====================================================================
    # Task result emission
    # =====================================================================

    def _last_outgoing_chat_text(self, chat_id: int, max_entries: int = 120) -> str:
        """Get the most recent outgoing chat message for this chat from log tail."""
        if not chat_id:
            return ""
        try:
            entries = self.memory.read_jsonl_tail("chat.jsonl", max_entries=max_entries)
        except Exception:
            log.debug("Failed to read chat tail for relevance guard", exc_info=True)
            return ""

        for entry in reversed(entries):
            try:
                if str(entry.get("direction", "")).lower() not in {"out", "outgoing"}:
                    continue
                if int(entry.get("chat_id") or 0) != int(chat_id):
                    continue
                txt = str(entry.get("text") or "").strip()
                if txt:
                    return txt
            except Exception:
                continue
        return ""

    def _rewrite_user_reply_for_relevance(
        self,
        current_text: str,
        *,
        task_text: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Rewrite a stale template-like reply into a direct answer for latest user question.

        Returns (rewritten_text, usage_dict). usage_dict can be empty on failure.
        """
        question = str(task_text or "").strip()
        draft = str(current_text or "").strip()
        if not question or not draft:
            return current_text, {}

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "Ты редактор ответа ассистента владельцу. "
                    "Перепиши черновик в ПРЯМОЙ ответ на последний вопрос пользователя.\n"
                    "Правила:\n"
                    "- Только русский язык.\n"
                    "- 2-5 предложений.\n"
                    "- Сразу отвечай на вопрос по сути.\n"
                    "- Без заголовков, списков, телеметрии, таймстампов и внутренних отчётов.\n"
                    "- Не пиши про фоновый цикл.\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Вопрос пользователя:\n{question}\n\n"
                    f"Черновик ответа (он шаблонный/устаревший, перепиши):\n{draft}\n\n"
                    "Верни только финальный ответ."
                ),
            },
        ]

        try:
            model = self.llm.default_model()
            msg, usage = self.llm.chat(
                messages=prompt_messages,
                model=model,
                tools=None,
                reasoning_effort="low",
                max_tokens=220,
            )
            rewritten = str(msg.get("content") or "").strip()
            if rewritten:
                return rewritten, usage or {}
            return current_text, usage or {}
        except Exception:
            log.debug("Relevance rewrite failed; fallback to deterministic text", exc_info=True)
            fallback = (
                "Понял. Я дал шаблонный статус вместо прямого ответа. "
                "Причина — повтор старого контекста, а не новый анализ вопроса. "
                "Исправляю это и дальше отвечаю сначала по сути, без шаблонных отчётов."
            )
            return fallback, {}

    def _apply_response_relevance_guard(
        self,
        user_text: str,
        task: Dict[str, Any],
        usage_total: Dict[str, Any],
        drive_logs: pathlib.Path,
    ) -> str:
        """
        Detect stale repeated status-template replies and rewrite them into a direct answer.
        """
        if _is_auto_resume_task(task):
            return user_text

        task_text = str(task.get("text") or "").strip()
        candidate = str(user_text or "").strip()
        chat_id = int(task.get("chat_id") or 0)
        if not task_text or not candidate or not chat_id:
            return user_text
        if not _is_status_template_reply(candidate):
            return user_text

        prev_out = self._last_outgoing_chat_text(chat_id)
        similarity = _text_similarity(candidate, prev_out)
        overlap_ratio = _response_focus_overlap_ratio(candidate, task_text)

        # Guard trigger:
        # - template-like reply,
        # - highly similar to previous outgoing text,
        # - and weak overlap with latest question focus.
        should_rewrite = similarity >= 0.88 and overlap_ratio < 0.45
        if not should_rewrite:
            return user_text

        rewritten, rewrite_usage = self._rewrite_user_reply_for_relevance(
            candidate,
            task_text=task_text,
        )
        if rewrite_usage:
            add_usage(usage_total, rewrite_usage)
            self._pending_events.append({
                "type": "llm_usage",
                "provider": "openrouter",
                "usage": rewrite_usage,
                "category": "task",
                "task_id": task.get("id"),
                "model": self.llm.default_model(),
                "ts": utc_now_iso(),
            })

        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "response_relevance_guard_rewrite",
            "task_id": task.get("id"),
            "chat_id": chat_id,
            "similarity_to_prev_out": round(float(similarity), 4),
            "focus_overlap_ratio": round(float(overlap_ratio), 4),
            "question_preview": truncate_for_log(task_text, 200),
        })
        return rewritten

    def _emit_task_results(
        self, task: Dict[str, Any], text: str,
        usage: Dict[str, Any], llm_trace: Dict[str, Any],
        start_time: float, drive_logs: pathlib.Path,
    ) -> None:
        """Emit all end-of-task events to supervisor."""
        # NOTE: per-round llm_usage events are already emitted in loop.py
        # (_emit_llm_usage_event). Do NOT emit an aggregate llm_usage here —
        # that would double-count in update_budget_from_usage.
        # Cost/token summaries are carried by task_metrics and task_done events.

        sanitized_for_user = _strip_background_preamble_for_user(text, task)
        user_text, log_text = _split_user_and_log_text(sanitized_for_user)
        user_text = self._apply_response_relevance_guard(user_text, task, usage, drive_logs)
        # Keep full model output for diagnostics even when user text is sanitized.
        log_text = str(text or "")
        self._pending_events.append({
            "type": "send_message", "chat_id": task["chat_id"],
            "text": user_text or "\u200b", "log_text": log_text or "",
            "format": "markdown",
            "task_id": task.get("id"), "ts": utc_now_iso(),
        })

        duration_sec = round(time.time() - start_time, 3)
        n_tool_calls = len(llm_trace.get("tool_calls", []))
        n_tool_errors = sum(1 for tc in llm_trace.get("tool_calls", [])
                            if isinstance(tc, dict) and tc.get("is_error"))
        try:
            append_jsonl(drive_logs / "events.jsonl", {
                "ts": utc_now_iso(), "type": "task_eval", "ok": True,
                "task_id": task.get("id"), "task_type": task.get("type"),
                "duration_sec": duration_sec,
                "tool_calls": n_tool_calls,
                "tool_errors": n_tool_errors,
                "response_len": len(text),
            })
        except Exception:
            log.warning("Failed to log task eval event", exc_info=True)
            pass

        self._pending_events.append({
            "type": "task_metrics",
            "task_id": task.get("id"), "task_type": task.get("type"),
            "duration_sec": duration_sec,
            "tool_calls": n_tool_calls, "tool_errors": n_tool_errors,
            "cost_usd": round(float(usage.get("cost") or 0), 6),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_rounds": int(usage.get("rounds") or 0),
            "ts": utc_now_iso(),
        })

        self._pending_events.append({
            "type": "task_done",
            "task_id": task.get("id"),
            "task_type": task.get("type"),
            "cost_usd": round(float(usage.get("cost") or 0), 6),
            "total_rounds": int(usage.get("rounds") or 0),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "ts": utc_now_iso(),
        })
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "task_done",
            "task_id": task.get("id"),
            "task_type": task.get("type"),
            "cost_usd": round(float(usage.get("cost") or 0), 6),
            "total_rounds": int(usage.get("rounds") or 0),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        })

        # Store task result for parent task retrieval
        try:
            results_dir = pathlib.Path(self.env.drive_root) / "task_results"
            results_dir.mkdir(parents=True, exist_ok=True)
            result_data = {
                "task_id": task.get("id"),
                "parent_task_id": task.get("parent_task_id"),
                "status": "completed",
                "result": text[:4000] if text else "",  # Truncate to avoid huge files
                "cost_usd": round(float(usage.get("cost") or 0), 6),
                "total_rounds": int(usage.get("rounds") or 0),
                "ts": utc_now_iso(),
            }
            result_file = results_dir / f"{task.get('id')}.json"
            tmp_file = results_dir / f"{task.get('id')}.json.tmp"
            tmp_file.write_text(json.dumps(result_data, ensure_ascii=False, indent=2))
            os.rename(tmp_file, result_file)
        except Exception as e:
            log.warning("Failed to store task result: %s", e)

    # =====================================================================
    # Review context builder
    # =====================================================================

    def _build_review_context(self) -> str:
        """Collect code snapshot + complexity metrics for review tasks."""
        try:
            from ouroboros.review import collect_sections, compute_complexity_metrics, format_metrics
            sections, stats = collect_sections(self.env.repo_dir, self.env.drive_root)
            metrics = compute_complexity_metrics(sections)

            parts = [
                "## Code Review Context\n",
                format_metrics(metrics),
                f"\nFiles: {stats['files']}, chars: {stats['chars']}\n",
                "\nUse repo_read to inspect specific files. "
                "Use run_shell for tests. Key files below:\n",
            ]

            total_chars = 0
            max_chars = 80_000
            files_added = 0
            for path, content in sections:
                if total_chars >= max_chars:
                    parts.append(f"\n... ({len(sections) - files_added} more files, use repo_read)")
                    break
                preview = content[:2000] if len(content) > 2000 else content
                file_block = f"\n### {path}\n```\n{preview}\n```\n"
                total_chars += len(file_block)
                parts.append(file_block)
                files_added += 1

            return "\n".join(parts)
        except Exception as e:
            return f"## Code Review Context\n\n(Failed to collect: {e})\nUse repo_read and repo_list to inspect code."

    # =====================================================================
    # Event emission helpers
    # =====================================================================

    def _emit_progress(self, text: str) -> None:
        self._last_progress_ts = time.time()
        if self._event_queue is None or self._current_chat_id is None:
            return
        try:
            self._event_queue.put({
                "type": "send_message", "chat_id": self._current_chat_id,
                "text": f"💬 {text}", "format": "markdown", "is_progress": True,
                "ts": utc_now_iso(),
            })
        except Exception:
            log.warning("Failed to emit progress event", exc_info=True)
            pass

    def _emit_typing_start(self) -> None:
        if self._event_queue is None or self._current_chat_id is None:
            return
        try:
            self._event_queue.put({
                "type": "typing_start", "chat_id": self._current_chat_id,
                "ts": utc_now_iso(),
            })
        except Exception:
            log.warning("Failed to emit typing start event", exc_info=True)
            pass

    def _emit_task_heartbeat(self, task_id: str, phase: str) -> None:
        if self._event_queue is None:
            return
        try:
            self._event_queue.put({
                "type": "task_heartbeat", "task_id": task_id,
                "phase": phase, "ts": utc_now_iso(),
            })
        except Exception:
            log.warning("Failed to emit task heartbeat event", exc_info=True)
            pass

    def _start_task_heartbeat_loop(self, task_id: str) -> Optional[threading.Event]:
        if self._event_queue is None or not task_id.strip():
            return None
        interval = 30
        stop = threading.Event()
        self._emit_task_heartbeat(task_id, "start")

        def _loop() -> None:
            while not stop.wait(interval):
                self._emit_task_heartbeat(task_id, "running")

        threading.Thread(target=_loop, daemon=True).start()
        return stop


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_agent(repo_dir: str, drive_root: str, event_queue: Any = None) -> OuroborosAgent:
    env = Env(repo_dir=pathlib.Path(repo_dir), drive_root=pathlib.Path(drive_root))
    return OuroborosAgent(env, event_queue=event_queue)
