"""
Ouroboros — LLM tool loop.

Core loop: send messages to LLM, execute tool calls, repeat until final response.
Extracted from agent.py to keep the agent thin.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import queue
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Dict, List, Optional, Tuple

from ouroboros.context import compact_tool_history, compact_tool_history_llm
from ouroboros.llm import (
    LLMClient,
    add_usage,
    get_code_model_from_env,
    get_fallback_models_from_env,
    get_free_models_from_env,
    get_paid_models_from_env,
    is_free_model,
    normalize_reasoning_effort,
)
from ouroboros.tool_args import parse_tool_call_arguments
from ouroboros.tools.registry import ToolRegistry
from ouroboros.utils import (
    append_jsonl,
    estimate_tokens,
    sanitize_tool_args_for_log,
    sanitize_tool_result_for_log,
    truncate_for_log,
    utc_now_iso,
)

log = logging.getLogger(__name__)

# Pricing from OpenRouter API (2026-02-17). Update periodically via /api/v1/models.
_MODEL_PRICING_STATIC = {
    "anthropic/claude-opus-4.6": (5.0, 0.5, 25.0),
    "anthropic/claude-opus-4": (15.0, 1.5, 75.0),
    "anthropic/claude-sonnet-4": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4.6": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4.5": (3.0, 0.30, 15.0),
    "openai/o3": (2.0, 0.50, 8.0),
    "openai/o3-pro": (20.0, 1.0, 80.0),
    "openai/o4-mini": (1.10, 0.275, 4.40),
    "openai/gpt-4.1": (2.0, 0.50, 8.0),
    "openai/gpt-5.2": (1.75, 0.175, 14.0),
    "openai/gpt-5.2-codex": (1.75, 0.175, 14.0),
    "google/gemini-2.5-pro-preview": (1.25, 0.125, 10.0),
    "google/gemini-3-pro-preview": (2.0, 0.20, 12.0),
    "x-ai/grok-3-mini": (0.30, 0.03, 0.50),
    "qwen/qwen3.5-plus-02-15": (0.40, 0.04, 2.40),
}

_pricing_fetched = False
_cached_pricing = None
_pricing_lock = threading.Lock()

def _get_pricing() -> Dict[str, Tuple[float, float, float]]:
    """
    Lazy-load pricing. On first call, attempts to fetch from OpenRouter API.
    Falls back to static pricing if fetch fails.
    Thread-safe via module-level lock.
    """
    global _pricing_fetched, _cached_pricing

    # Fast path: already fetched (read without lock for performance)
    if _pricing_fetched:
        return _cached_pricing or _MODEL_PRICING_STATIC

    # Slow path: fetch pricing (lock required)
    with _pricing_lock:
        # Double-check after acquiring lock (another thread may have fetched)
        if _pricing_fetched:
            return _cached_pricing or _MODEL_PRICING_STATIC

        _pricing_fetched = True
        _cached_pricing = dict(_MODEL_PRICING_STATIC)

        try:
            from ouroboros.llm import fetch_openrouter_pricing
            _live = fetch_openrouter_pricing()
            if _live and len(_live) > 5:
                _cached_pricing.update(_live)
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("Failed to sync pricing from OpenRouter: %s", e)
            # Reset flag so we retry next time
            _pricing_fetched = False

        return _cached_pricing

def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                   cached_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Estimate cost from token counts using known pricing. Returns 0 if model unknown."""
    model_pricing = _get_pricing()
    # Try exact match first
    pricing = model_pricing.get(model)
    if not pricing:
        # Try longest prefix match
        best_match = None
        best_length = 0
        for key, val in model_pricing.items():
            if model and model.startswith(key):
                if len(key) > best_length:
                    best_match = val
                    best_length = len(key)
        pricing = best_match
    if not pricing:
        return 0.0
    input_price, cached_price, output_price = pricing
    # Non-cached input tokens = prompt_tokens - cached_tokens
    regular_input = max(0, prompt_tokens - cached_tokens)
    cost = (
        regular_input * input_price / 1_000_000
        + cached_tokens * cached_price / 1_000_000
        + completion_tokens * output_price / 1_000_000
    )
    return round(cost, 6)

READ_ONLY_PARALLEL_TOOLS = frozenset({
    "repo_read", "repo_list",
    "drive_read", "drive_list",
    "web_search", "codebase_digest", "chat_history",
})

# Read-only tools that often appear in "analysis loops" with no concrete output.
READ_ONLY_LOOP_TOOLS = frozenset({
    "repo_read", "repo_list",
    "drive_read", "drive_list",
    "git_status", "git_diff",
    "chat_history", "codebase_digest",
    "knowledge_list", "knowledge_read",
    "list_available_tools",
})

# Stateful browser tools require thread-affinity (Playwright sync uses greenlet)
STATEFUL_BROWSER_TOOLS = frozenset({"browse_page", "browser_action"})


def _truncate_tool_result(result: Any) -> str:
    """
    Hard-cap tool result string to 15000 characters.
    If truncated, append a note with the original length.
    """
    result_str = str(result)
    if len(result_str) <= 15000:
        return result_str
    original_len = len(result_str)
    return result_str[:15000] + f"\n... (truncated from {original_len} chars)"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default


def _is_complex_task_type(task_type: str) -> bool:
    return str(task_type or "").strip().lower() in {
        "review",
        "evolution",
        "arch_review",
        "summarize",
    }


def _pick_initial_model(default_model: str, task_type: str, *, is_direct_chat: bool = False) -> str:
    """
    Pick initial model:
    - direct chat: main/default model,
    - non-direct worker tasks: code model when configured,
    - complex tasks: paid candidate fallback,
    - fallback: default model.
    """
    if is_direct_chat:
        return default_model

    code_model = get_code_model_from_env()
    if code_model and code_model != default_model:
        return code_model

    paid_candidates = get_paid_models_from_env(active_model="")
    free_candidates = get_free_models_from_env(active_model="")

    if _is_complex_task_type(task_type) and paid_candidates:
        return paid_candidates[0]
    if free_candidates:
        return free_candidates[0]
    if paid_candidates:
        return paid_candidates[0]
    return default_model


def _next_paid_candidate(active_model: str, cooldown_until: Dict[str, float]) -> Optional[str]:
    now = time.monotonic()
    for model in get_paid_models_from_env(active_model=active_model):
        if cooldown_until.get(model, 0.0) > now:
            continue
        return model
    return None


def _is_paid_limit_error(error: Exception) -> bool:
    text = str(error or "").lower()
    markers = (
        "insufficient",
        "insufficient credit",
        "not enough credit",
        "payment required",
        "billing",
        "quota",
        "limit reached",
        "credit balance",
        "out of credits",
        "key limit exceeded",
        "total limit",
        "402",
    )
    return any(m in text for m in markers)


_DEFAULT_MODEL_CONTEXT_WINDOW = 200_000
_MODEL_CONTEXT_WINDOWS = {
    # Per-provider prefixes; approximate defaults for proactive context safety.
    "x-ai/grok-4.1-fast": 2_000_000,
    "anthropic/": 200_000,
    "openai/": 200_000,
    "google/": 1_000_000,
}


def _estimate_context_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate token size for current message history (content + tool call args)."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if text:
                    total += estimate_tokens(str(text))

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                total += estimate_tokens(str(fn.get("name") or ""))
                total += estimate_tokens(str(fn.get("arguments") or ""))

    return max(1, total)


def _resolve_model_context_window(model: str) -> int:
    """Resolve model context window with env overrides and safe defaults."""
    direct_override = _env_int("OUROBOROS_MODEL_CONTEXT_WINDOW", _DEFAULT_MODEL_CONTEXT_WINDOW)
    if direct_override > 0 and str(os.environ.get("OUROBOROS_MODEL_CONTEXT_WINDOW", "")).strip():
        return direct_override

    model_name = str(model or "").strip()

    # Optional map override:
    # OUROBOROS_MODEL_CONTEXT_WINDOWS="x-ai/grok-4.1-fast=2000000,anthropic/=200000"
    mapping_raw = str(os.environ.get("OUROBOROS_MODEL_CONTEXT_WINDOWS", "") or "").strip()
    if mapping_raw:
        best_len = -1
        best_val: Optional[int] = None
        for chunk in mapping_raw.split(","):
            item = chunk.strip()
            if not item or "=" not in item:
                continue
            prefix, raw_limit = item.split("=", 1)
            prefix = prefix.strip()
            try:
                limit = int(raw_limit.strip())
            except (TypeError, ValueError):
                continue
            if limit <= 0:
                continue
            if model_name.startswith(prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best_val = limit
        if best_val is not None:
            return best_val

    best_prefix = ""
    best_window = _DEFAULT_MODEL_CONTEXT_WINDOW
    for prefix, window in _MODEL_CONTEXT_WINDOWS.items():
        if model_name.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_window = window
    return best_window


def _loop_guard_hard_stop_allowed(active_model: str) -> bool:
    """
    Decide whether repeated-loop guards should hard-stop the task.

    Default policy:
    - paid models: hard-stop enabled
    - free models: try recovery first, no hard-stop
    """
    if _env_bool("OUROBOROS_LOOP_GUARD_ALWAYS_STOP", default=False):
        return True
    if is_free_model(active_model):
        return _env_bool("OUROBOROS_LOOP_GUARD_STOP_ON_FREE", default=False)
    return True


def _loop_guard_hard_stop(active_model: str, *, is_direct_chat: bool = False) -> bool:
    """
    Final hard-stop policy for repeated-loop guards.

    In direct chat we default to hard-stop to avoid long recover spirals.
    """
    if is_direct_chat and _env_bool("OUROBOROS_LOOP_GUARD_FORCE_STOP_DIRECT_CHAT", default=True):
        return True
    return _loop_guard_hard_stop_allowed(active_model)


def _loop_guard_free_recovery_limit() -> int:
    return max(1, _env_int("OUROBOROS_LOOP_GUARD_FREE_RECOVERY_LIMIT", 3))


def _run_shell_exit_code(result: Any) -> Optional[int]:
    """Extract run_shell exit code from tool result prefix ('exit_code=N')."""
    text = str(result or "")
    if not text.startswith("exit_code="):
        return None
    first_line = text.splitlines()[0].strip()
    raw_code = first_line.split("=", 1)[1].strip() if "=" in first_line else ""
    try:
        return int(raw_code)
    except (TypeError, ValueError):
        return None


def _tool_result_is_error(fn_name: str, result: Any) -> bool:
    """Classify tool results that should be treated as errors."""
    text = str(result or "")
    if text.startswith("⚠️"):
        return True
    if fn_name == "run_shell":
        exit_code = _run_shell_exit_code(text)
        return (exit_code is not None) and (exit_code != 0)
    return False


def _append_thinking_trace(
    drive_logs: pathlib.Path,
    *,
    source: str,
    step: str,
    task_id: str = "",
    task_type: str = "",
    round_idx: int = 0,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a structured thought-like trace event (observable decisions only)."""
    payload = {
        "ts": utc_now_iso(),
        "source": source,
        "step": step,
    }
    if task_id:
        payload["task_id"] = task_id
    if task_type:
        payload["task_type"] = task_type
    if round_idx > 0:
        payload["round"] = round_idx
    if details:
        safe_details: Dict[str, Any] = {}
        for k, v in details.items():
            if isinstance(v, str):
                safe_details[k] = truncate_for_log(v, 1200)
            else:
                safe_details[k] = v
        payload["details"] = safe_details
    try:
        append_jsonl(drive_logs / "thinking_trace.jsonl", payload)
    except Exception:
        log.debug("Failed to append thinking_trace", exc_info=True)


def _execute_single_tool(
    tools: ToolRegistry,
    tc: Dict[str, Any],
    drive_logs: pathlib.Path,
    task_id: str = "",
) -> Dict[str, Any]:
    """
    Execute a single tool call and return all needed info.

    Returns dict with: tool_call_id, fn_name, result, is_error, args_for_log, is_code_tool
    """
    fn_name = tc["function"]["name"]
    tool_call_id = tc["id"]
    is_code_tool = fn_name in tools.CODE_TOOLS

    # Parse arguments
    try:
        args = parse_tool_call_arguments(tc["function"].get("arguments"))
    except (json.JSONDecodeError, ValueError) as e:
        result = f"⚠️ TOOL_ARG_ERROR: Could not parse arguments for '{fn_name}': {e}"
        return {
            "tool_call_id": tool_call_id,
            "fn_name": fn_name,
            "result": result,
            "is_error": True,
            "args_for_log": {},
            "is_code_tool": is_code_tool,
        }

    args_for_log = sanitize_tool_args_for_log(fn_name, args if isinstance(args, dict) else {})

    # Execute tool
    tool_ok = True
    try:
        result = tools.execute(fn_name, args)
    except Exception as e:
        tool_ok = False
        result = f"⚠️ TOOL_ERROR ({fn_name}): {type(e).__name__}: {e}"
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(), "type": "tool_error", "task_id": task_id,
            "tool": fn_name, "args": args_for_log, "error": repr(e),
        })

    # Log tool execution (sanitize secrets from result before persisting)
    append_jsonl(drive_logs / "tools.jsonl", {
        "ts": utc_now_iso(), "tool": fn_name, "task_id": task_id,
        "args": args_for_log,
        "result_preview": sanitize_tool_result_for_log(truncate_for_log(result, 2000)),
    })

    is_error = (not tool_ok) or _tool_result_is_error(fn_name, result)

    return {
        "tool_call_id": tool_call_id,
        "fn_name": fn_name,
        "result": result,
        "is_error": is_error,
        "args_for_log": args_for_log,
        "is_code_tool": is_code_tool,
    }


class _StatefulToolExecutor:
    """
    Thread-sticky executor for stateful tools (browser, etc).

    Playwright sync API uses greenlet internally which has strict thread-affinity:
    once a greenlet starts in a thread, all subsequent calls must happen in the same thread.
    This executor ensures browse_page/browser_action always run in the same thread.

    On timeout: we shutdown the executor and create a fresh one to reset state.
    """
    def __init__(self):
        self._executor: Optional[ThreadPoolExecutor] = None

    def submit(self, fn, *args, **kwargs):
        """Submit work to the sticky thread. Creates executor on first call."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stateful_tool")
        return self._executor.submit(fn, *args, **kwargs)

    def reset(self):
        """Shutdown current executor and create a fresh one. Used after timeout/error."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def shutdown(self, wait=True, cancel_futures=False):
        """Final cleanup."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
            self._executor = None


def _make_timeout_result(
    fn_name: str,
    tool_call_id: str,
    is_code_tool: bool,
    tc: Dict[str, Any],
    drive_logs: pathlib.Path,
    timeout_sec: int,
    task_id: str = "",
    reset_msg: str = "",
) -> Dict[str, Any]:
    """
    Create a timeout error result dictionary and log the timeout event.

    Args:
        reset_msg: Optional additional message (e.g., "Browser state has been reset. ")

    Returns: Dict with tool_call_id, fn_name, result, is_error, args_for_log, is_code_tool
    """
    args_for_log = {}
    try:
        args = parse_tool_call_arguments(tc["function"].get("arguments"))
        args_for_log = sanitize_tool_args_for_log(fn_name, args if isinstance(args, dict) else {})
    except Exception:
        pass

    result = (
        f"⚠️ TOOL_TIMEOUT ({fn_name}): exceeded {timeout_sec}s limit. "
        f"The tool is still running in background but control is returned to you. "
        f"{reset_msg}Try a different approach or inform the owner{' about the issue' if not reset_msg else ''}."
    )

    append_jsonl(drive_logs / "events.jsonl", {
        "ts": utc_now_iso(), "type": "tool_timeout",
        "tool": fn_name, "args": args_for_log,
        "timeout_sec": timeout_sec,
    })
    append_jsonl(drive_logs / "tools.jsonl", {
        "ts": utc_now_iso(), "tool": fn_name,
        "args": args_for_log, "result_preview": result,
    })

    return {
        "tool_call_id": tool_call_id,
        "fn_name": fn_name,
        "result": result,
        "is_error": True,
        "args_for_log": args_for_log,
        "is_code_tool": is_code_tool,
    }


def _execute_with_timeout(
    tools: ToolRegistry,
    tc: Dict[str, Any],
    drive_logs: pathlib.Path,
    timeout_sec: int,
    task_id: str = "",
    stateful_executor: Optional[_StatefulToolExecutor] = None,
) -> Dict[str, Any]:
    """
    Execute a tool call with a hard timeout.

    On timeout: returns TOOL_TIMEOUT error so the LLM regains control.
    For stateful tools (browser): resets the sticky executor to recover state.
    For regular tools: the hung worker thread leaks as daemon — watchdog handles recovery.
    """
    fn_name = tc["function"]["name"]
    tool_call_id = tc["id"]
    is_code_tool = fn_name in tools.CODE_TOOLS
    use_stateful = stateful_executor and fn_name in STATEFUL_BROWSER_TOOLS

    # Two distinct paths: stateful (thread-sticky) vs regular (per-call)
    if use_stateful:
        # Stateful executor: submit + wait, reset on timeout
        future = stateful_executor.submit(_execute_single_tool, tools, tc, drive_logs, task_id)
        try:
            return future.result(timeout=timeout_sec)
        except (TimeoutError, FuturesTimeoutError):
            stateful_executor.reset()
            reset_msg = "Browser state has been reset. "
            return _make_timeout_result(
                fn_name, tool_call_id, is_code_tool, tc, drive_logs,
                timeout_sec, task_id, reset_msg
            )
    else:
        # Regular executor: explicit lifecycle to avoid shutdown(wait=True) deadlock
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_execute_single_tool, tools, tc, drive_logs, task_id)
            try:
                return future.result(timeout=timeout_sec)
            except (TimeoutError, FuturesTimeoutError):
                return _make_timeout_result(
                    fn_name, tool_call_id, is_code_tool, tc, drive_logs,
                    timeout_sec, task_id, reset_msg=""
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _handle_tool_calls(
    tool_calls: List[Dict[str, Any]],
    tools: ToolRegistry,
    drive_logs: pathlib.Path,
    task_id: str,
    stateful_executor: _StatefulToolExecutor,
    messages: List[Dict[str, Any]],
    llm_trace: Dict[str, Any],
    emit_progress: Callable[[str], None],
    round_idx: int,
    task_type: str = "task",
) -> int:
    """
    Execute tool calls and append results to messages.

    Returns: Number of errors encountered
    """
    # Parallelize only for a strict read-only whitelist; all calls wrapped with timeout.
    can_parallel = (
        len(tool_calls) > 1 and
        all(
            tc.get("function", {}).get("name") in READ_ONLY_PARALLEL_TOOLS
            for tc in tool_calls
        )
    )

    if not can_parallel:
        results = [
            _execute_with_timeout(tools, tc, drive_logs,
                                  tools.get_timeout(tc["function"]["name"]), task_id,
                                  stateful_executor)
            for tc in tool_calls
        ]
    else:
        max_workers = min(len(tool_calls), 8)
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_to_index = {
                executor.submit(
                    _execute_with_timeout, tools, tc, drive_logs,
                    tools.get_timeout(tc["function"]["name"]), task_id,
                    stateful_executor,
                ): idx
                for idx, tc in enumerate(tool_calls)
            }
            results = [None] * len(tool_calls)
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                results[idx] = future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # Process results in original order
    return _process_tool_results(
        results,
        messages,
        llm_trace,
        emit_progress,
        drive_logs,
        task_id,
        round_idx,
        task_type,
        tools=tools,
    )


def _handle_text_response(
    content: Optional[str],
    llm_trace: Dict[str, Any],
    accumulated_usage: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Handle LLM response without tool calls (final response).

    Returns: (final_text, accumulated_usage, llm_trace)
    """
    text = str(content or "")
    low = text.lower()
    if "<tool_call>" in low and "</tool_call>" in low:
        text = (
            "⚠️ Остановил цикл: модель вернула служебный tool_call вместо ответа. "
            "Сформулируй запрос ещё раз, отвечу коротко и по делу."
        )
    if text.strip():
        llm_trace["assistant_notes"].append(text.strip()[:320])
    return text, accumulated_usage, llm_trace


def _check_budget_limits(
    budget_remaining_usd: Optional[float],
    accumulated_usage: Dict[str, Any],
    round_idx: int,
    messages: List[Dict[str, Any]],
    llm: LLMClient,
    active_model: str,
    active_effort: str,
    max_retries: int,
    drive_logs: pathlib.Path,
    task_id: str,
    event_queue: Optional[queue.Queue],
    llm_trace: Dict[str, Any],
    task_type: str = "task",
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """
    Legacy compatibility shim (budget guard removed).

    Returns:
        Always None. Budget no longer controls loop termination/model routing.
    """
    return None


def _maybe_inject_self_check(
    round_idx: int,
    max_rounds: int,
    messages: List[Dict[str, Any]],
    accumulated_usage: Dict[str, Any],
    emit_progress: Callable[[str], None],
) -> None:
    """Inject a soft self-check reminder every REMINDER_INTERVAL rounds.

    This is a cognitive feature (Bible P0: subjectivity) — the agent reflects
    on its own resource usage and strategy, not a hard kill.
    """
    REMINDER_INTERVAL = 50
    if round_idx <= 1 or round_idx % REMINDER_INTERVAL != 0:
        return
    ctx_tokens = _estimate_context_tokens(messages)
    task_cost = accumulated_usage.get("cost", 0)
    checkpoint_num = round_idx // REMINDER_INTERVAL

    reminder = (
        f"[CHECKPOINT {checkpoint_num} — round {round_idx}/{max_rounds}]\n"
        f"📊 Context: ~{ctx_tokens} tokens | Cost so far: ${task_cost:.2f} | "
        f"Rounds remaining: {max_rounds - round_idx}\n\n"
        f"⏸️ PAUSE AND REFLECT before continuing:\n"
        f"1. Am I making real progress, or repeating the same actions?\n"
        f"2. Is my current strategy working? Should I try something different?\n"
        f"3. Is my context bloated with old tool results I no longer need?\n"
        f"   → If yes, call `compact_context` to summarize them selectively.\n"
        f"4. Have I been stuck on the same sub-problem for many rounds?\n"
        f"   → If yes, consider: simplify the approach, skip the sub-problem, or finish with what I have.\n"
        f"5. Should I just STOP and return my best result so far?\n\n"
        f"This is not a hard limit — you decide. But be honest with yourself."
    )
    messages.append({"role": "system", "content": reminder})
    emit_progress(f"🔄 Checkpoint {checkpoint_num} at round {round_idx}: ~{ctx_tokens} tokens, ${task_cost:.2f} spent")


def _maybe_auto_compact_context(
    *,
    round_idx: int,
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    active_model: str,
    drive_logs: pathlib.Path,
    task_id: str,
    task_type: str,
    emit_progress: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Auto-compact context when estimated usage crosses threshold.

    Implements proactive compaction after LLM/tool rounds:
    if context exceeds threshold percentage of model window, schedule
    `compact_context(auto=True)` and apply compaction immediately.
    """
    if not _env_bool("OUROBOROS_AUTO_CONTEXT_COMPACT", default=True):
        return messages

    context_window = _resolve_model_context_window(active_model)
    if context_window <= 0:
        return messages

    threshold_pct = _env_float("OUROBOROS_AUTO_CONTEXT_COMPACT_AT_PCT", 45.0)
    threshold_pct = max(10.0, min(threshold_pct, 95.0))
    threshold_tokens = int(context_window * (threshold_pct / 100.0))
    ctx_tokens = _estimate_context_tokens(messages)
    if ctx_tokens < threshold_tokens:
        return messages

    cooldown_rounds = max(1, _env_int("OUROBOROS_AUTO_CONTEXT_COMPACT_COOLDOWN_ROUNDS", 2))
    last_round = int(getattr(tools._ctx, "_last_auto_compact_round", 0) or 0)
    if round_idx - last_round < cooldown_rounds:
        return messages

    keep_last_n = max(2, min(_env_int("OUROBOROS_AUTO_CONTEXT_KEEP_LAST_N", 6), 20))

    try:
        # Route through the tool interface to preserve uniform behavior.
        tools.execute("compact_context", {"keep_last_n": keep_last_n, "auto": True})
        pending_compaction = getattr(tools._ctx, "_pending_compaction", None)
        if pending_compaction is None:
            return messages

        compacted = compact_tool_history_llm(messages, keep_recent=pending_compaction)
        tools._ctx._pending_compaction = None
        tools._ctx._last_auto_compact_round = round_idx

        _append_thinking_trace(
            drive_logs,
            source="task_loop",
            step="auto_context_compaction",
            task_id=task_id,
            task_type=task_type or "task",
            round_idx=round_idx,
            details={
                "auto": True,
                "model": active_model,
                "context_tokens_before": ctx_tokens,
                "context_window": context_window,
                "threshold_pct": threshold_pct,
                "keep_last_n": pending_compaction,
            },
        )
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "auto_context_compaction",
            "task_id": task_id,
            "round": round_idx,
            "task_type": task_type or "task",
            "auto": True,
            "model": active_model,
            "context_tokens_before": int(ctx_tokens),
            "context_window": int(context_window),
            "threshold_pct": float(threshold_pct),
            "keep_last_n": int(pending_compaction),
        })
        emit_progress(
            f"🧠 Auto compact_context: ~{ctx_tokens}/{context_window} tokens "
            f"({threshold_pct:.0f}%+), keep_last_n={pending_compaction}"
        )
        return compacted
    except Exception as e:
        log.warning("Auto context compaction failed: %s", e, exc_info=True)
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "auto_context_compaction_error",
            "task_id": task_id,
            "round": round_idx,
            "task_type": task_type or "task",
            "error": repr(e),
        })
        return messages


def _setup_dynamic_tools(tools_registry, tool_schemas, messages):
    """
    Wire tool-discovery handlers onto an existing tool_schemas list.

    Creates closures for list_available_tools / enable_tools, registers them
    as handler overrides, and injects a system message advertising non-core
    tools.  Mutates tool_schemas in-place (via list.append) when tools are
    enabled, so the caller's reference stays live.

    Returns (tool_schemas, enabled_extra_set).
    """
    enabled_extra: set = set()

    def _handle_list_tools(ctx=None, **kwargs):
        non_core = tools_registry.list_non_core_tools()
        if not non_core:
            return "All tools are already in your active set."
        lines = [f"**{len(non_core)} additional tools available** (use `enable_tools` to activate):\n"]
        for t in non_core:
            lines.append(f"- **{t['name']}**: {t['description'][:120]}")
        return "\n".join(lines)

    def _handle_enable_tools(ctx=None, tools: str = "", **kwargs):
        names = [n.strip() for n in tools.split(",") if n.strip()]
        enabled, not_found = [], []
        for name in names:
            schema = tools_registry.get_schema_by_name(name)
            if schema and name not in enabled_extra:
                tool_schemas.append(schema)
                enabled_extra.add(name)
                enabled.append(name)
            elif name in enabled_extra:
                enabled.append(f"{name} (already active)")
            else:
                not_found.append(name)
        parts = []
        if enabled:
            parts.append(f"✅ Enabled: {', '.join(enabled)}")
        if not_found:
            parts.append(f"❌ Not found: {', '.join(not_found)}")
        return "\n".join(parts) if parts else "No tools specified."

    tools_registry.override_handler("list_available_tools", _handle_list_tools)
    tools_registry.override_handler("enable_tools", _handle_enable_tools)

    non_core_count = len(tools_registry.list_non_core_tools())
    if non_core_count > 0:
        messages.append({
            "role": "system",
            "content": (
                f"Note: You have {len(tool_schemas)} core tools loaded. "
                f"There are {non_core_count} additional tools available "
                f"(use `list_available_tools` to see them, `enable_tools` to activate). "
                f"Core tools cover most tasks. Enable extras only when needed."
            ),
        })

    return tool_schemas, enabled_extra


def _drain_incoming_messages(
    messages: List[Dict[str, Any]],
    incoming_messages: queue.Queue,
    drive_root: Optional[pathlib.Path],
    task_id: str,
    event_queue: Optional[queue.Queue],
    _owner_msg_seen: set,
) -> int:
    """
    Inject owner messages received during task execution.
    Drains both the in-process queue and the Drive mailbox.
    """
    injected_count = 0

    # Inject owner messages received during task execution
    while not incoming_messages.empty():
        try:
            injected = incoming_messages.get_nowait()
            messages.append({"role": "user", "content": injected})
            injected_count += 1
        except queue.Empty:
            break

    # Drain per-task owner messages from Drive mailbox (written by forward_to_worker tool)
    if drive_root is not None and task_id:
        from ouroboros.owner_inject import drain_owner_messages
        drive_msgs = drain_owner_messages(drive_root, task_id=task_id, seen_ids=_owner_msg_seen)
        for dmsg in drive_msgs:
            messages.append({
                "role": "user",
                "content": f"[Owner message during task]: {dmsg}",
            })
            injected_count += 1
            # Log for duplicate processing detection (health invariant #5)
            if event_queue is not None:
                try:
                    event_queue.put_nowait({
                        "type": "owner_message_injected",
                        "task_id": task_id,
                        "text": dmsg[:200],
                    })
                except Exception:
                    pass

    return injected_count


def run_llm_loop(
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    llm: LLMClient,
    drive_logs: pathlib.Path,
    emit_progress: Callable[[str], None],
    incoming_messages: queue.Queue,
    task_type: str = "",
    task_id: str = "",
    budget_remaining_usd: Optional[float] = None,
    event_queue: Optional[queue.Queue] = None,
    initial_effort: str = "medium",
    drive_root: Optional[pathlib.Path] = None,
    is_direct_chat: bool = False,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Core LLM-with-tools loop.

    Sends messages to LLM, executes tool calls, retries on errors.
    LLM controls model/effort via switch_model tool (LLM-first, Bible P3).

    Args:
        initial_effort: Initial reasoning effort level (default "medium")

    Returns: (final_text, accumulated_usage, llm_trace)
    """
    # Budget-driven stopping is deprecated; keep param for backward compatibility.
    _ = budget_remaining_usd

    # Model router:
    # - default to free list for normal tasks,
    # - start with paid list for explicitly complex tasks.
    active_model = _pick_initial_model(
        llm.default_model(),
        task_type,
        is_direct_chat=is_direct_chat,
    )
    active_effort = initial_effort

    llm_trace: Dict[str, Any] = {"assistant_notes": [], "tool_calls": []}
    accumulated_usage: Dict[str, Any] = {}
    max_retries = 3
    paid_cooldown_sec = max(120, _env_int("OUROBOROS_PAID_MODEL_COOLDOWN_SEC", 1800))
    paid_model_cooldown_until: Dict[str, float] = {}
    # Wire module-level registry ref so tool_discovery handlers work outside run_llm_loop too
    from ouroboros.tools import tool_discovery as _td
    _td.set_registry(tools)

    # Selective tool schemas: core set + meta-tools for discovery.
    tool_schemas = tools.schemas(core_only=True)
    tool_schemas, _enabled_extra_tools = _setup_dynamic_tools(tools, tool_schemas, messages)

    # Set budget tracking on tool context for real-time usage events
    tools._ctx.event_queue = event_queue
    tools._ctx.task_id = task_id
    # Thread-sticky executor for browser tools (Playwright sync requires greenlet thread-affinity)
    stateful_executor = _StatefulToolExecutor()
    # Dedup set for per-task owner messages from Drive mailbox
    _owner_msg_seen: set = set()
    # Anti-loop guards: repeated errors and repeated identical tool batches.
    recent_error_sigs: deque[str] = deque(maxlen=12)
    recent_batch_sigs: deque[str] = deque(maxlen=12)
    free_guard_recovery_limit = _loop_guard_free_recovery_limit()
    free_error_recoveries = 0
    free_batch_recoveries = 0

    def _try_escalate_to_paid(reason: str, round_no: int) -> bool:
        nonlocal active_model, active_effort, free_error_recoveries, free_batch_recoveries
        if not is_free_model(active_model):
            return False
        target = _next_paid_candidate(active_model, paid_model_cooldown_until)
        if not target:
            return False
        prev_model = active_model
        active_model = target
        # Paid escalation is for "hard mode": increase reasoning effort conservatively.
        active_effort = normalize_reasoning_effort("high", default=active_effort)
        free_error_recoveries = 0
        free_batch_recoveries = 0
        recent_error_sigs.clear()
        recent_batch_sigs.clear()
        emit_progress(f"🚀 Эскалация на платную модель: {prev_model} → {target} ({reason})")
        _append_thinking_trace(
            drive_logs,
            source="task_loop",
            step="escalate_to_paid_model",
            task_id=task_id,
            task_type=task_type or "task",
            round_idx=round_no,
            details={"from": prev_model, "to": target, "reason": reason},
        )
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "model_escalation",
            "task_id": task_id,
            "task_type": task_type or "task",
            "reason": reason,
            "from_model": prev_model,
            "to_model": target,
            "round": int(round_no),
        })
        return True

    def _on_llm_api_error(error_model: str, error: Exception) -> bool:
        model_name = str(error_model or "").strip()
        if not model_name or is_free_model(model_name):
            return False
        if not _is_paid_limit_error(error):
            return False
        cooldown_until = time.monotonic() + float(paid_cooldown_sec)
        prev = float(paid_model_cooldown_until.get(model_name, 0.0))
        if cooldown_until > prev:
            paid_model_cooldown_until[model_name] = cooldown_until
            append_jsonl(drive_logs / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "paid_model_cooldown",
                "task_id": task_id,
                "model": model_name,
                "cooldown_sec": int(paid_cooldown_sec),
                "reason": truncate_for_log(str(error), 240),
            })
        return True
    try:
        repeated_error_threshold = max(2, int(os.environ.get("OUROBOROS_REPEAT_ERROR_THRESHOLD", "4")))
    except (ValueError, TypeError):
        repeated_error_threshold = 4
        log.warning("Invalid OUROBOROS_REPEAT_ERROR_THRESHOLD, defaulting to 4")

    try:
        repeated_error_window_min = max(4, int(os.environ.get("OUROBOROS_REPEAT_ERROR_WINDOW_MIN", "6")))
    except (ValueError, TypeError):
        repeated_error_window_min = 6
        log.warning("Invalid OUROBOROS_REPEAT_ERROR_WINDOW_MIN, defaulting to 6")
    try:
        repeated_batch_threshold = max(3, int(os.environ.get("OUROBOROS_REPEAT_TOOL_BATCH_THRESHOLD", "8")))
    except (ValueError, TypeError):
        repeated_batch_threshold = 8
        log.warning("Invalid OUROBOROS_REPEAT_TOOL_BATCH_THRESHOLD, defaulting to 8")
    try:
        repeated_batch_window_min = max(4, int(os.environ.get("OUROBOROS_REPEAT_TOOL_BATCH_WINDOW_MIN", "10")))
    except (ValueError, TypeError):
        repeated_batch_window_min = 10
        log.warning("Invalid OUROBOROS_REPEAT_TOOL_BATCH_WINDOW_MIN, defaulting to 10")
    try:
        MAX_ROUNDS = max(1, int(os.environ.get("OUROBOROS_MAX_ROUNDS", "200")))
    except (ValueError, TypeError):
        MAX_ROUNDS = 200
        log.warning("Invalid OUROBOROS_MAX_ROUNDS, defaulting to 200")
    direct_chat_max_rounds = max(20, _env_int("OUROBOROS_DIRECT_CHAT_MAX_ROUNDS", 80))
    effective_max_rounds = min(MAX_ROUNDS, direct_chat_max_rounds) if is_direct_chat else MAX_ROUNDS
    owner_interrupt_grace_rounds = max(1, _env_int("OUROBOROS_OWNER_INTERRUPT_GRACE_ROUNDS", 3))
    owner_interrupt_deadline_round: Optional[int] = None
    read_only_streak = 0
    read_only_streak_threshold = max(12, _env_int("OUROBOROS_DIRECT_CHAT_READ_ONLY_STREAK_STOP", 24))
    round_idx = 0
    try:
        while True:
            round_idx += 1
            _append_thinking_trace(
                drive_logs,
                source="task_loop",
                step="round_start",
                task_id=task_id,
                task_type=task_type or "task",
                round_idx=round_idx,
                details={
                    "active_model": active_model,
                    "reasoning_effort": active_effort,
                    "message_count": len(messages),
                },
            )

            # Hard limit on rounds to prevent runaway tasks
            if round_idx > effective_max_rounds:
                finish_reason = (
                    f"⚠️ Task exceeded max rounds ({effective_max_rounds}). "
                    "Останавливаю цикл и формирую финальный ответ по текущему прогрессу."
                )
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="round_limit_reached",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"max_rounds": effective_max_rounds, "finish_reason": finish_reason},
                )
                messages.append({"role": "system", "content": f"[ROUND_LIMIT] {finish_reason}"})
                try:
                    final_msg, _ = _call_llm_with_retry(
                        llm, messages, active_model, None, active_effort,
                        max_retries, drive_logs, task_id, round_idx, event_queue, accumulated_usage, task_type,
                        on_api_error=_on_llm_api_error,
                    )
                    if final_msg:
                        return (final_msg.get("content") or finish_reason), accumulated_usage, llm_trace
                    return finish_reason, accumulated_usage, llm_trace
                except Exception:
                    log.warning("Failed to get final response after round limit", exc_info=True)
                    return finish_reason, accumulated_usage, llm_trace

            # Soft self-check reminder every 50 rounds (LLM-first: agent decides, not code)
            _maybe_inject_self_check(round_idx, effective_max_rounds, messages, accumulated_usage, emit_progress)

            # Apply LLM-driven model/effort switch (via switch_model tool)
            ctx = tools._ctx
            if ctx.active_model_override:
                prev_model = active_model
                active_model = ctx.active_model_override
                ctx.active_model_override = None
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="model_switch",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"from": prev_model, "to": active_model},
                )
            if ctx.active_effort_override:
                prev_effort = active_effort
                active_effort = normalize_reasoning_effort(ctx.active_effort_override, default=active_effort)
                ctx.active_effort_override = None
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="effort_switch",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"from": prev_effort, "to": active_effort},
                )

            # Inject owner messages (in-process queue + Drive mailbox)
            injected_owner_count = _drain_incoming_messages(
                messages, incoming_messages, drive_root, task_id, event_queue, _owner_msg_seen
            )
            if injected_owner_count > 0 and is_direct_chat:
                owner_interrupt_deadline_round = round_idx + owner_interrupt_grace_rounds
                emit_progress("📩 Получил новое сообщение во время задачи. Перехожу к приоритетному ответу.")
                messages.append({
                    "role": "system",
                    "content": (
                        "[OWNER_INTERRUPT] Во время выполнения пришло новое сообщение владельца. "
                        "Сверни текущую диагностику и подготовь прямой ответ по последнему сообщению. "
                        "Избегай длинных серий read-only инструментов."
                    ),
                })

            if (
                is_direct_chat
                and owner_interrupt_deadline_round is not None
                and round_idx >= owner_interrupt_deadline_round
            ):
                force_msg = (
                    "⚠️ Прерываю длинную задачу: пришло новое сообщение владельца, "
                    "нужен немедленный ответ без дальнейших инструментов."
                )
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="owner_interrupt_force_finalize",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"grace_rounds": owner_interrupt_grace_rounds},
                )
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(),
                    "type": "task_loop_guard_stop",
                    "task_id": task_id,
                    "reason": "owner_interrupt_force_finalize",
                    "round": int(round_idx),
                })
                messages.append({
                    "role": "system",
                    "content": (
                        "[FORCE_FINALIZE] Ответь пользователю СЕЙЧАС. "
                        "Не вызывай инструменты. Дай короткий статус и следующий шаг."
                    ),
                })
                try:
                    final_msg, _ = _call_llm_with_retry(
                        llm, messages, active_model, None, active_effort,
                        max_retries, drive_logs, task_id, round_idx, event_queue, accumulated_usage, task_type,
                        on_api_error=_on_llm_api_error,
                    )
                    if final_msg and str(final_msg.get("content") or "").strip():
                        return str(final_msg.get("content") or ""), accumulated_usage, llm_trace
                    return force_msg, accumulated_usage, llm_trace
                except Exception:
                    log.warning("Forced finalize on owner interrupt failed", exc_info=True)
                    return force_msg, accumulated_usage, llm_trace

            # Compact old tool history when needed
            # Check for LLM-requested compaction first (via compact_context tool)
            pending_compaction = getattr(tools._ctx, '_pending_compaction', None)
            if pending_compaction is not None:
                messages = compact_tool_history_llm(messages, keep_recent=pending_compaction)
                tools._ctx._pending_compaction = None
            elif round_idx > 8:
                messages = compact_tool_history(messages, keep_recent=6)
            elif round_idx > 3:
                # Light compaction: only if messages list is very long (>60 items)
                if len(messages) > 60:
                    messages = compact_tool_history(messages, keep_recent=6)

            # --- LLM call with retry ---
            msg, _ = _call_llm_with_retry(
                llm, messages, active_model, tool_schemas, active_effort,
                max_retries, drive_logs, task_id, round_idx, event_queue, accumulated_usage, task_type,
                on_api_error=_on_llm_api_error,
            )

            # Fallback to another model if primary model returns empty responses
            if msg is None:
                # Only allow fallback to models explicitly configured via env.
                now_mono = time.monotonic()
                fallback_candidates = []
                for candidate in get_fallback_models_from_env(active_model=active_model):
                    if (not is_free_model(candidate)) and paid_model_cooldown_until.get(candidate, 0.0) > now_mono:
                        continue
                    fallback_candidates.append(candidate)
                fallback_model = fallback_candidates[0] if fallback_candidates else None
                if fallback_model is None:
                    if _try_escalate_to_paid("free_model_failed_to_respond", round_idx):
                        continue
                    _append_thinking_trace(
                        drive_logs,
                        source="task_loop",
                        step="fallback_failed",
                        task_id=task_id,
                        task_type=task_type or "task",
                        round_idx=round_idx,
                        details={"active_model": active_model, "reason": "no_distinct_fallback_model"},
                    )
                    return (
                        f"⚠️ Failed to get a response from model {active_model} after {max_retries} attempts. "
                        f"All fallback models match the active one. Try rephrasing your request."
                    ), accumulated_usage, llm_trace

                # Emit progress message so user sees fallback happening
                fallback_progress = f"⚡ Fallback: {active_model} → {fallback_model} after empty response"
                emit_progress(fallback_progress)
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="fallback_model_selected",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"from": active_model, "to": fallback_model},
                )

                # Try fallback model (don't increment round_idx — this is still same logical round)
                msg, _ = _call_llm_with_retry(
                    llm, messages, fallback_model, tool_schemas, active_effort,
                    max_retries, drive_logs, task_id, round_idx, event_queue, accumulated_usage, task_type,
                    on_api_error=_on_llm_api_error,
                )

                # If fallback also fails, give up
                if msg is None:
                    _append_thinking_trace(
                        drive_logs,
                        source="task_loop",
                        step="fallback_failed",
                        task_id=task_id,
                        task_type=task_type or "task",
                        round_idx=round_idx,
                        details={"fallback_model": fallback_model, "reason": "empty_response_after_retries"},
                    )
                    return (
                        f"⚠️ Failed to get a response from the model after {max_retries} attempts. "
                        f"Fallback model ({fallback_model}) also returned no response."
                    ), accumulated_usage, llm_trace

                # Fallback succeeded — continue processing with this msg
                # Persist fallback model for subsequent rounds to avoid re-trying
                # the unavailable/empty primary model every round.
                prev_model = active_model
                active_model = fallback_model
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="fallback_model_promoted",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"from": prev_model, "to": active_model},
                )

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content")
            _append_thinking_trace(
                drive_logs,
                source="task_loop",
                step="llm_response",
                task_id=task_id,
                task_type=task_type or "task",
                round_idx=round_idx,
                details={
                    "assistant_preview": (content or "")[:300],
                    "tool_count": len(tool_calls),
                    "tool_names": [tc.get("function", {}).get("name", "") for tc in tool_calls[:20]],
                },
            )
            # No tool calls — final response
            if not tool_calls:
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="final_response",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={"response_preview": (content or "")[:400]},
                )
                return _handle_text_response(content, llm_trace, accumulated_usage)

            # Process tool calls
            messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})

            if content and content.strip():
                emit_progress(content.strip())
                llm_trace["assistant_notes"].append(content.strip()[:320])

            error_count = _handle_tool_calls(
                tool_calls, tools, drive_logs, task_id, stateful_executor,
                messages, llm_trace, emit_progress, round_idx, task_type or "task"
            )
            _append_thinking_trace(
                drive_logs,
                source="task_loop",
                step="tool_batch_done",
                task_id=task_id,
                task_type=task_type or "task",
                round_idx=round_idx,
                details={"tool_count": len(tool_calls), "error_count": int(error_count)},
            )
            messages = _maybe_auto_compact_context(
                round_idx=round_idx,
                messages=messages,
                tools=tools,
                active_model=active_model,
                drive_logs=drive_logs,
                task_id=task_id,
                task_type=task_type or "task",
                emit_progress=emit_progress,
            )

            # Direct-chat safety: stop long read-only spirals early.
            recent_batch = (llm_trace.get("tool_calls") or [])[-len(tool_calls):]
            if (
                recent_batch
                and all(
                    isinstance(item, dict)
                    and (not bool(item.get("is_error")))
                    and str(item.get("tool") or "") in READ_ONLY_LOOP_TOOLS
                    for item in recent_batch
                )
            ):
                read_only_streak += 1
            else:
                read_only_streak = 0

            if is_direct_chat and read_only_streak >= read_only_streak_threshold:
                final = (
                    "⚠️ Остановил зациклившуюся диагностику: слишком много подряд read-only шагов "
                    "без финального ответа. Дай краткий статус и чёткий следующий шаг."
                )
                _append_thinking_trace(
                    drive_logs,
                    source="task_loop",
                    step="direct_chat_read_only_guard_stop",
                    task_id=task_id,
                    task_type=task_type or "task",
                    round_idx=round_idx,
                    details={
                        "read_only_streak": int(read_only_streak),
                        "threshold": int(read_only_streak_threshold),
                    },
                )
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(),
                    "type": "task_loop_guard_stop",
                    "task_id": task_id,
                    "reason": "direct_chat_read_only_spiral",
                    "read_only_streak": int(read_only_streak),
                    "threshold": int(read_only_streak_threshold),
                })
                messages.append({
                    "role": "system",
                    "content": (
                        "[ANTI_LOOP_DIRECT_CHAT] Ты застрял в read-only диагностике. "
                        "Немедленно дай финальный ответ владельцу. Без инструментов."
                    ),
                })
                try:
                    final_msg, _ = _call_llm_with_retry(
                        llm, messages, active_model, None, active_effort,
                        max_retries, drive_logs, task_id, round_idx, event_queue, accumulated_usage, task_type,
                        on_api_error=_on_llm_api_error,
                    )
                    if final_msg and str(final_msg.get("content") or "").strip():
                        return str(final_msg.get("content") or ""), accumulated_usage, llm_trace
                    return final, accumulated_usage, llm_trace
                except Exception:
                    log.warning("Direct-chat read-only guard finalize failed", exc_info=True)
                    return final, accumulated_usage, llm_trace

            # Detect repeated failure patterns (e.g. same Unknown tool) and stop early.
            current_error_sig = _batch_error_signature(llm_trace, len(tool_calls))
            if current_error_sig:
                recent_error_sigs.append(current_error_sig)
                if len(recent_error_sigs) >= repeated_error_window_min:
                    top_sig, top_count = Counter(recent_error_sigs).most_common(1)[0]
                    if top_count >= repeated_error_threshold:
                        hard_stop = _loop_guard_hard_stop(
                            active_model,
                            is_direct_chat=is_direct_chat,
                        )
                        if (not hard_stop) and free_error_recoveries < free_guard_recovery_limit:
                            free_error_recoveries += 1
                            recover_msg = (
                                "♻️ Обнаружил повтор ошибок инструментов на free-модели. "
                                "Меняю стратегию и продолжаю."
                            )
                            emit_progress(recover_msg)
                            messages.append({
                                "role": "system",
                                "content": (
                                    "[ANTI_LOOP] Ты повторяешь одни и те же ошибки инструментов. "
                                    "Немедленно смени подход: не вызывай тот же инструмент с теми же аргументами. "
                                    "Либо выбери другой инструмент/последовательность, либо дай финальный ответ с "
                                    "чётким объяснением ограничения."
                                ),
                            })
                            recent_error_sigs.clear()
                            _append_thinking_trace(
                                drive_logs,
                                source="task_loop",
                                step="repeated_error_guard_recover",
                                task_id=task_id,
                                task_type=task_type or "task",
                                round_idx=round_idx,
                                details={
                                    "mode": "continue",
                                    "active_model": active_model,
                                    "recovery_no": free_error_recoveries,
                                    "recovery_limit": free_guard_recovery_limit,
                                },
                            )
                            append_jsonl(drive_logs / "events.jsonl", {
                                "ts": utc_now_iso(),
                                "type": "task_loop_guard_recover",
                                "task_id": task_id,
                                "reason": "repeated_tool_errors",
                                "mode": "continue",
                                "model": active_model,
                                "recovery_no": int(free_error_recoveries),
                            })
                            continue
                        if (not hard_stop) and _try_escalate_to_paid("repeated_tool_errors", round_idx):
                            continue
                        final = (
                            f"⚠️ Обнаружены повторяющиеся ошибки инструментов ({top_count} повторов за "
                            f"{len(recent_error_sigs)} раундов). "
                            f"Последняя сигнатура: {truncate_for_log(top_sig, 260)}. "
                            "Останавливаю текущий цикл. Нужна новая формулировка или изменение ограничений."
                        )
                        _append_thinking_trace(
                            drive_logs,
                            source="task_loop",
                            step="repeated_error_guard_stop",
                            task_id=task_id,
                            task_type=task_type or "task",
                            round_idx=round_idx,
                            details={
                                "top_error_signature": top_sig,
                                "top_error_count": top_count,
                                "window_size": len(recent_error_sigs),
                                "threshold": repeated_error_threshold,
                            },
                        )
                        append_jsonl(drive_logs / "events.jsonl", {
                            "ts": utc_now_iso(),
                            "type": "task_loop_guard_stop",
                            "task_id": task_id,
                            "reason": "repeated_tool_errors",
                            "error_signature": truncate_for_log(top_sig, 500),
                            "repeat_count": int(top_count),
                            "window_size": int(len(recent_error_sigs)),
                        })
                        return final, accumulated_usage, llm_trace

            # Detect repeated identical tool batches (same args + same results).
            current_batch_sig = _batch_tool_signature(llm_trace, len(tool_calls))
            if current_batch_sig:
                recent_batch_sigs.append(current_batch_sig)
                stop, top_sig, top_count = _should_stop_on_repeated_signature(
                    recent_batch_sigs,
                    threshold=repeated_batch_threshold,
                    window_min=repeated_batch_window_min,
                )
                if stop:
                    hard_stop = _loop_guard_hard_stop(
                        active_model,
                        is_direct_chat=is_direct_chat,
                    )
                    if (not hard_stop) and free_batch_recoveries < free_guard_recovery_limit:
                        free_batch_recoveries += 1
                        recover_msg = (
                            "♻️ Обнаружил цикл одинаковых действий на free-модели. "
                            "Сменил стратегию и продолжаю."
                        )
                        emit_progress(recover_msg)
                        messages.append({
                            "role": "system",
                            "content": (
                                "[ANTI_LOOP] Ты повторяешь один и тот же пакет инструментов с одинаковыми аргументами "
                                "и результатами. Немедленно измени план: не повторяй этот пакет. "
                                "Либо используй другие инструменты, либо дай финальный ответ с тем, что блокирует задачу."
                            ),
                        })
                        recent_batch_sigs.clear()
                        _append_thinking_trace(
                            drive_logs,
                            source="task_loop",
                            step="repeated_tool_guard_recover",
                            task_id=task_id,
                            task_type=task_type or "task",
                            round_idx=round_idx,
                            details={
                                "mode": "continue",
                                "active_model": active_model,
                                "recovery_no": free_batch_recoveries,
                                "recovery_limit": free_guard_recovery_limit,
                            },
                        )
                        append_jsonl(drive_logs / "events.jsonl", {
                            "ts": utc_now_iso(),
                            "type": "task_loop_guard_recover",
                            "task_id": task_id,
                            "reason": "repeated_tool_batch",
                            "mode": "continue",
                            "model": active_model,
                            "recovery_no": int(free_batch_recoveries),
                        })
                        continue
                    if (not hard_stop) and _try_escalate_to_paid("repeated_tool_batch", round_idx):
                        continue
                    final = (
                        f"⚠️ Обнаружен цикл одинаковых действий ({top_count} повторов за "
                        f"{len(recent_batch_sigs)} раундов). "
                        f"Сигнатура: {truncate_for_log(top_sig, 260)}. "
                        "Останавливаю текущий цикл. Нужна смена подхода или уточнение ограничений."
                    )
                    _append_thinking_trace(
                        drive_logs,
                        source="task_loop",
                        step="repeated_tool_guard_stop",
                        task_id=task_id,
                        task_type=task_type or "task",
                        round_idx=round_idx,
                        details={
                            "top_batch_signature": top_sig,
                            "top_batch_count": top_count,
                            "window_size": len(recent_batch_sigs),
                            "threshold": repeated_batch_threshold,
                        },
                    )
                    append_jsonl(drive_logs / "events.jsonl", {
                        "ts": utc_now_iso(),
                        "type": "task_loop_guard_stop",
                        "task_id": task_id,
                        "reason": "repeated_tool_batch",
                        "batch_signature": truncate_for_log(top_sig, 500),
                        "repeat_count": int(top_count),
                        "window_size": int(len(recent_batch_sigs)),
                    })
                    return final, accumulated_usage, llm_trace

    finally:
        # Cleanup thread-sticky executor for stateful tools
        if stateful_executor:
            try:
                stateful_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                log.warning("Failed to shutdown stateful executor", exc_info=True)
        # Cleanup per-task mailbox
        if drive_root is not None and task_id:
            try:
                from ouroboros.owner_inject import cleanup_task_mailbox
                cleanup_task_mailbox(drive_root, task_id)
            except Exception:
                log.debug("Failed to cleanup task mailbox", exc_info=True)


def _emit_llm_usage_event(
    event_queue: Optional[queue.Queue],
    task_id: str,
    model: str,
    usage: Dict[str, Any],
    cost: float,
    category: str = "task",
) -> None:
    """
    Emit llm_usage event to the event queue.

    Args:
        event_queue: Queue to emit events to (may be None)
        task_id: Task ID for the event
        model: Model name used for the LLM call
        usage: Usage dict from LLM response
        cost: Calculated cost for this call
        category: Budget category (task, evolution, consciousness, review, summarize, other)
    """
    if not event_queue:
        return
    try:
        event_queue.put_nowait({
            "type": "llm_usage",
            "ts": utc_now_iso(),
            "task_id": task_id,
            "model": model,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_tokens": int(usage.get("cached_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "cost": cost,
            "cost_estimated": not bool(usage.get("cost")),
            "usage": usage,
            "category": category,
        })
    except Exception:
        log.debug("Failed to put llm_usage event to queue", exc_info=True)


def _call_llm_with_retry(
    llm: LLMClient,
    messages: List[Dict[str, Any]],
    model: str,
    tools: Optional[List[Dict[str, Any]]],
    effort: str,
    max_retries: int,
    drive_logs: pathlib.Path,
    task_id: str,
    round_idx: int,
    event_queue: Optional[queue.Queue],
    accumulated_usage: Dict[str, Any],
    task_type: str = "",
    on_api_error: Optional[Callable[[str, Exception], bool]] = None,
) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Call LLM with retry logic, usage tracking, and event emission.

    Returns:
        (response_message, cost) on success
        (None, 0.0) on failure after max_retries
    """
    msg = None
    for attempt in range(max_retries):
        try:
            kwargs = {"messages": messages, "model": model, "reasoning_effort": effort}
            if tools:
                kwargs["tools"] = tools
            resp_msg, usage = llm.chat(**kwargs)
            msg = resp_msg
            add_usage(accumulated_usage, usage)

            # Calculate cost and emit event for EVERY attempt (including retries)
            cost = float(usage.get("cost") or 0)
            if not cost:
                cost = _estimate_cost(
                    model,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("cached_tokens") or 0),
                    int(usage.get("cache_write_tokens") or 0),
                )

            # Emit real-time usage event with category based on task_type
            category = task_type if task_type in ("evolution", "consciousness", "review", "summarize") else "task"
            _emit_llm_usage_event(event_queue, task_id, model, usage, cost, category)

            # Empty response = retry-worthy (model sometimes returns empty content with no tool_calls)
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content")
            if not tool_calls and (not content or not content.strip()):
                log.warning("LLM returned empty response (no content, no tool_calls), attempt %d/%d", attempt + 1, max_retries)

                # Log raw empty response for debugging
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(), "type": "llm_empty_response",
                    "task_id": task_id,
                    "round": round_idx, "attempt": attempt + 1,
                    "model": model,
                    "raw_content": repr(content)[:500] if content else None,
                    "raw_tool_calls": repr(tool_calls)[:500] if tool_calls else None,
                    "finish_reason": msg.get("finish_reason") or msg.get("stop_reason"),
                })

                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                # Last attempt — return None to trigger "could not get response"
                return None, cost

            # Count only successful rounds
            accumulated_usage["rounds"] = accumulated_usage.get("rounds", 0) + 1

            # Log per-round metrics
            _round_event = {
                "ts": utc_now_iso(), "type": "llm_round",
                "task_id": task_id,
                "round": round_idx, "model": model,
                "reasoning_effort": effort,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "cached_tokens": int(usage.get("cached_tokens") or 0),
                "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
                "cost_usd": cost,
            }
            append_jsonl(drive_logs / "events.jsonl", _round_event)
            return msg, cost

        except Exception as e:
            stop_retries = False
            if on_api_error is not None:
                try:
                    stop_retries = bool(on_api_error(model, e))
                except Exception:
                    log.debug("on_api_error callback failed", exc_info=True)
            append_jsonl(drive_logs / "events.jsonl", {
                "ts": utc_now_iso(), "type": "llm_api_error",
                "task_id": task_id,
                "round": round_idx, "attempt": attempt + 1,
                "model": model, "error": repr(e),
            })
            if stop_retries:
                return None, 0.0
            if attempt < max_retries - 1:
                time.sleep(min(2 ** attempt * 2, 30))

    return None, 0.0


def _process_tool_results(
    results: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    llm_trace: Dict[str, Any],
    emit_progress: Callable[[str], None],
    drive_logs: pathlib.Path,
    task_id: str,
    round_idx: int,
    task_type: str = "task",
    tools: Optional[ToolRegistry] = None,
) -> int:
    """
    Process tool execution results and append to messages/trace.

    Args:
        results: List of tool execution result dicts
        messages: Message list to append tool results to
        llm_trace: Trace dict to append tool call info to
        emit_progress: Callback for progress updates

    Returns:
        Number of errors encountered
    """
    error_count = 0

    for exec_result in results:
        fn_name = exec_result["fn_name"]
        is_error = exec_result["is_error"]

        if is_error:
            error_count += 1

        # Truncate tool result before appending to messages
        truncated_result = _truncate_tool_result(exec_result["result"])

        # Append tool result message
        messages.append({
            "role": "tool",
            "tool_call_id": exec_result["tool_call_id"],
            "content": truncated_result
        })

        # Append to LLM trace
        llm_trace["tool_calls"].append({
            "tool": fn_name,
            "args": _safe_args(exec_result["args_for_log"]),
            "result": truncate_for_log(exec_result["result"], 700),
            "is_error": is_error,
        })
        _append_thinking_trace(
            drive_logs,
            source="task_loop",
            step="tool_result",
            task_id=task_id,
            task_type=task_type,
            round_idx=round_idx,
            details={
                "tool": fn_name,
                "is_error": bool(is_error),
                "args": _safe_args(exec_result["args_for_log"]),
                "result_preview": truncate_for_log(str(exec_result["result"]), 700),
            },
        )

        auto_commit = _maybe_auto_commit_after_code_tool(
            tools=tools,
            source_tool=fn_name,
            source_is_error=bool(is_error),
            drive_logs=drive_logs,
            task_id=task_id,
            task_type=task_type,
            round_idx=round_idx,
            emit_progress=emit_progress,
        )
        if auto_commit:
            messages.append({
                "role": "system",
                "content": (
                    "[AUTO_COMMIT] "
                    + truncate_for_log(str(auto_commit.get("result") or ""), 900)
                ),
            })
            llm_trace["tool_calls"].append({
                "tool": str(auto_commit.get("tool") or "repo_commit_push"),
                "args": _safe_args(auto_commit.get("args") or {}),
                "result": truncate_for_log(str(auto_commit.get("result") or ""), 700),
                "is_error": bool(auto_commit.get("is_error")),
            })
            if bool(auto_commit.get("is_error")):
                error_count += 1

    return error_count


def _auto_commit_enabled() -> bool:
    return _env_bool("OUROBOROS_AUTO_COMMIT_AFTER_EDIT", default=True)


def _auto_commit_tool_set() -> set[str]:
    raw = str(
        os.environ.get("OUROBOROS_AUTO_COMMIT_TOOLS", "opencode_edit,run_shell") or ""
    ).strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _auto_commit_message(source_tool: str, task_id: str, round_idx: int) -> str:
    template = str(
        os.environ.get(
            "OUROBOROS_AUTO_COMMIT_MESSAGE",
            "auto: persist changes after {tool} (task={task_id} round={round})",
        )
        or ""
    ).strip()
    if not template:
        template = "auto: persist changes after {tool} (task={task_id} round={round})"
    try:
        msg = template.format(tool=source_tool, task_id=task_id, round=round_idx)
    except Exception:
        msg = f"auto: persist changes after {source_tool} (task={task_id} round={round_idx})"
    msg = str(msg).strip()
    return msg or f"auto: persist changes after {source_tool}"


def _maybe_auto_commit_after_code_tool(
    *,
    tools: Optional[ToolRegistry],
    source_tool: str,
    source_is_error: bool,
    drive_logs: pathlib.Path,
    task_id: str,
    task_type: str,
    round_idx: int,
    emit_progress: Callable[[str], None],
) -> Optional[Dict[str, Any]]:
    """
    Auto-commit dirty repo after successful code-edit tool execution.

    Returns a synthetic repo_commit_push result dict (for trace/context), or None.
    """
    if tools is None or source_is_error:
        return None
    if not _auto_commit_enabled():
        return None

    source = str(source_tool or "").strip()
    if source not in _auto_commit_tool_set():
        return None

    try:
        status = str(tools.execute("git_status", {}))
    except Exception as e:
        _append_thinking_trace(
            drive_logs,
            source="task_loop",
            step="auto_commit_status_error",
            task_id=task_id,
            task_type=task_type,
            round_idx=round_idx,
            details={"tool": source, "error": truncate_for_log(repr(e), 260)},
        )
        return None

    if not status.strip() or status.startswith("⚠️"):
        return None

    commit_message = _auto_commit_message(source, task_id, round_idx)
    commit_args = {"commit_message": commit_message}
    args_for_log = sanitize_tool_args_for_log("repo_commit_push", commit_args)

    try:
        commit_result = str(tools.execute("repo_commit_push", commit_args))
    except Exception as e:
        commit_result = f"⚠️ TOOL_ERROR (repo_commit_push): {type(e).__name__}: {e}"

    is_error = commit_result.startswith("⚠️")
    append_jsonl(
        drive_logs / "tools.jsonl",
        {
            "ts": utc_now_iso(),
            "tool": "repo_commit_push",
            "task_id": task_id,
            "args": args_for_log,
            "result_preview": sanitize_tool_result_for_log(
                truncate_for_log(commit_result, 2000)
            ),
            "auto": True,
            "source_tool": source,
        },
    )
    _append_thinking_trace(
        drive_logs,
        source="task_loop",
        step="auto_commit_result",
        task_id=task_id,
        task_type=task_type,
        round_idx=round_idx,
        details={
            "source_tool": source,
            "is_error": is_error,
            "result_preview": truncate_for_log(commit_result, 500),
        },
    )
    emit_progress(
        "Автокоммит после правки: "
        + ("успешно." if not is_error else "ошибка, см. результат.")
    )
    return {
        "tool": "repo_commit_push",
        "args": args_for_log,
        "result": commit_result,
        "is_error": is_error,
    }


def _safe_args(v: Any) -> Any:
    """Ensure args are JSON-serializable for trace logging."""
    try:
        return json.loads(json.dumps(v, ensure_ascii=False, default=str))
    except Exception:
        log.debug("Failed to serialize args for trace logging", exc_info=True)
        return {"_repr": repr(v)}


def _batch_error_signature(llm_trace: Dict[str, Any], batch_size: int) -> str:
    """Build a stable signature for error results in the latest tool batch."""
    if batch_size <= 0:
        return ""
    tool_calls = llm_trace.get("tool_calls") or []
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""

    recent = tool_calls[-batch_size:]
    sigs: List[str] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("is_error")):
            continue
        tool = str(item.get("tool") or "unknown")
        result = str(item.get("result") or "")
        sigs.append(f"{tool}: {truncate_for_log(result, 220)}")
    if not sigs:
        return ""
    # Sort + dedupe for stability when parallel tool execution changes order.
    return " | ".join(sorted(set(sigs)))


def _batch_tool_signature(llm_trace: Dict[str, Any], batch_size: int) -> str:
    """Build a stable signature for all results in the latest tool batch."""
    if batch_size <= 0:
        return ""
    tool_calls = llm_trace.get("tool_calls") or []
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""
    recent = tool_calls[-batch_size:]
    sigs: List[str] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "unknown")
        is_error = int(bool(item.get("is_error")))
        args_json = json.dumps(
            _safe_args(item.get("args")),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        result = truncate_for_log(str(item.get("result") or ""), 220)
        sigs.append(
            f"{tool}|err={is_error}|args={truncate_for_log(args_json, 200)}|res={result}"
        )
    if not sigs:
        return ""
    return " || ".join(sorted(set(sigs)))


def _should_stop_on_repeated_signature(
    recent_sigs: deque[str],
    *,
    threshold: int,
    window_min: int,
) -> Tuple[bool, str, int]:
    """Decide whether a repeated-signature guard should stop the task."""
    if len(recent_sigs) < max(1, int(window_min)):
        return False, "", 0
    non_empty = [s for s in recent_sigs if s]
    if not non_empty:
        return False, "", 0
    top_sig, top_count = Counter(non_empty).most_common(1)[0]
    return top_count >= max(1, int(threshold)), top_sig, int(top_count)
