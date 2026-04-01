"""
Ouroboros — LLM tool loop parallel executor.

Execute tool calls and append results to messages.

Returns: Number of errors encountered
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from ouroboros.timeout_handler import _execute_with_timeout

log = logging.getLogger(__name__)


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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default


READ_ONLY_PARALLEL_TOOLS = frozenset({
    "repo_read", "repo_list",
    "drive_read", "drive_list",
    "web_search", "codebase_digest", "chat_history",
})


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


def _batch_error_signature(llm_trace: Dict[str, Any], tool_count: int) -> Optional[str]:
    """Generate a signature for the current batch of tool errors."""
    tool_calls = llm_trace.get("tool_calls", [])[-tool_count:]
    error_sigs = []
    for tc in tool_calls:
        if tc.get("is_error"):
            error_sigs.append(f"{tc.get('fn_name')}: {tc.get('result')[:100]}")
    return "; ".join(error_sigs) if error_sigs else None


def _batch_tool_signature(llm_trace: Dict[str, Any], tool_count: int) -> Optional[str]:
    """Generate a signature for the current batch of tool calls."""
    tool_calls = llm_trace.get("tool_calls", [])[-tool_count:]
    if not tool_calls:
        return None
    sigs = []
    for tc in tool_calls:
        args = json.loads(tc.get("args_for_log", "{}") or "{}")
        sig = f"{tc.get('fn_name')}({len(args)})"
        sigs.append(sig)
    return "; ".join(sigs)


def _should_stop_on_repeated_signature(
    recent_sigs: deque[str],
    threshold: int = 8,
    window_min: int = 10,
) -> Tuple[bool, str, int]:
    """Check if we should stop due to repeated signatures."""
    if len(recent_sigs) < window_min:
        return False, "", 0
    counter = Counter(recent_sigs)
    most_common = counter.most_common(1)
    if most_common:
        top_sig, top_count = most_common[0]
        if top_count >= threshold:
            return True, top_sig, top_count
    return False, "", 0


def _process_tool_results(
    results: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    llm_trace: Dict[str, Any],
    emit_progress: Callable[[str], None],
    drive_logs: pathlib.Path,
    task_id: str,
    round_idx: int,
    task_type: str = "task",
) -> int:
    """
    Process tool results and append to messages.

    Returns: Number of errors encountered
    """
    error_count = 0
    for result in results:
        if result["is_error"]:
            error_count += 1
        llm_trace["tool_calls"].append(result)
        messages.append({
            "role": "tool",
            "content": f"[{result['fn_name']}] {result['result']}",
            "tool_call_id": result["tool_call_id"],
            "tool_name": result["fn_name"],
        })

    if error_count == len(results):
        emit_progress(f"⚠️ All {len(results)} tool calls failed in this batch")

    from ouroboros.utils import append_jsonl
    append_jsonl(drive_logs / "events.jsonl", {
        "ts": os.environ.get("NOW_ISO"),
        "type": "tool_batch_done",
        "task_id": task_id,
        "tool_count": len(results),
        "error_count": error_count,
        "round": round_idx,
        "task_type": task_type,
    })

    return error_count


def _handle_tool_calls(
    tool_calls: List[Dict[str, Any]],
    tools: Any,
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
    )


def _emit_llm_usage_event(
    event_queue: Optional[Any],
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
            "ts": os.environ.get("NOW_ISO"),
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
    llm: Any,
    messages: List[Dict[str, Any]],
    model: str,
    tools: Optional[List[Dict[str, Any]]],
    effort: str,
    max_retries: int,
    drive_logs: pathlib.Path,
    task_id: str,
    round_idx: int,
    event_queue: Optional[Any],
    accumulated_usage: Dict[str, Any],
    task_type: str = "",
    prompt_cache_key: str = "",
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
            kwargs = {
                "messages": messages,
                "model": model,
                "reasoning_effort": effort,
                "prompt_cache_key": prompt_cache_key,
            }
            if tools:
                kwargs["tools"] = tools
            resp_msg, usage = llm.chat(**kwargs)
            msg = resp_msg

            # Calculate cost and emit event for EVERY attempt (including retries)
            cost = float(usage.get("cost") or 0)
            if not cost:
                from ouroboros.pricing import _estimate_cost
                cost = _estimate_cost(
                    model,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("cached_tokens") or 0),
                    int(usage.get("cache_write_tokens") or 0),
                )
                usage["cost"] = cost

            from ouroboros.llm import add_usage
            add_usage(accumulated_usage, usage)

            # Emit real-time usage event with category based on task_type
            category = task_type if task_type in ("evolution", "consciousness", "review", "summarize") else "task"
            _emit_llm_usage_event(event_queue, task_id, model, usage, cost, category)

            # Empty response = retry-worthy (model sometimes returns empty content with no tool_calls)
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content")
            if not tool_calls and (not content or not content.strip()):
                log.warning("LLM returned empty response (no content, no tool_calls), attempt %d/%d", attempt + 1, max_retries)

                # Log raw empty response for debugging
                from ouroboros.utils import append_jsonl
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": os.environ.get("NOW_ISO"), "type": "llm_empty_response",
                    "task_id": task_id,
                    "round": round_idx, "attempt": attempt + 1,
                    "model": model,
                    "raw_content": repr(content)[:500] if content else None,
                    "raw_tool_calls": repr(tool_calls)[:500] if tool_calls else None,
                    "finish_reason": msg.get("finish_reason") or msg.get("stop_reason"),
                })

                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                # Last attempt — return None to trigger "could not get response"
                return None, cost

            # Count only successful rounds
            accumulated_usage["rounds"] = accumulated_usage.get("rounds", 0) + 1

            # Log per-round metrics
            append_jsonl(drive_logs / "events.jsonl", {
                "ts": os.environ.get("NOW_ISO"), "type": "llm_round",
                "task_id": task_id,
                "round": round_idx, "model": model,
                "reasoning_effort": effort,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "cached_tokens": int(usage.get("cached_tokens") or 0),
                "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
                "cost_usd": cost,
            })
            return msg, cost

        except Exception as e:
            from ouroboros.utils import append_jsonl
            append_jsonl(drive_logs / "events.jsonl", {
                "ts": os.environ.get("NOW_ISO"), "type": "llm_api_error",
                "task_id": task_id,
                "round": round_idx, "attempt": attempt + 1,
                "model": model,
                "error": repr(e)[:500],
            })

    # If we get here, all retries failed
    return None, 0.0
