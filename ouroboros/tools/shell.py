"""Shell tools: run_shell, opencode_edit."""

from __future__ import annotations

import difflib
import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import append_jsonl, run_cmd, truncate_for_log, utc_now_iso

log = logging.getLogger(__name__)


def _run_shell(ctx: ToolContext, cmd, cwd: str = "") -> str:
    # Recover from LLM sending cmd as JSON string instead of list
    if isinstance(cmd, str):
        raw_cmd = cmd
        warning = "run_shell_cmd_string"
        try:
            parsed = json.loads(cmd)
            if isinstance(parsed, list):
                cmd = parsed
                warning = "run_shell_cmd_string_json_list_recovered"
            elif isinstance(parsed, str):
                try:
                    cmd = shlex.split(parsed)
                except ValueError:
                    cmd = parsed.split()
                warning = "run_shell_cmd_string_json_string_split"
            else:
                try:
                    cmd = shlex.split(cmd)
                except ValueError:
                    cmd = cmd.split()
                warning = "run_shell_cmd_string_json_non_list_split"
        except Exception:
            try:
                cmd = shlex.split(cmd)
            except ValueError:
                cmd = cmd.split()
            warning = "run_shell_cmd_string_split_fallback"

        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "tool_warning",
                "tool": "run_shell",
                "warning": warning,
                "cmd_preview": truncate_for_log(raw_cmd, 500),
            })
        except Exception:
            log.debug("Failed to log run_shell warning to events.jsonl", exc_info=True)
            pass

    if not isinstance(cmd, list):
        return "⚠️ SHELL_ARG_ERROR: cmd must be a list of strings."
    cmd = [str(x) for x in cmd]

    work_dir = ctx.repo_dir
    if cwd and cwd.strip() not in ("", ".", "./"):
        candidate = (ctx.repo_dir / cwd).resolve()
        if candidate.exists() and candidate.is_dir():
            work_dir = candidate

    try:
        res = subprocess.run(
            cmd, cwd=str(work_dir),
            capture_output=True, text=True, timeout=120,
        )
        out = res.stdout + ("\n--- STDERR ---\n" + res.stderr if res.stderr else "")
        if len(out) > 50000:
            out = out[:25000] + "\n...(truncated)...\n" + out[-25000:]
        prefix = f"exit_code={res.returncode}\n"
        return prefix + out
    except subprocess.TimeoutExpired:
        return "⚠️ TIMEOUT: command exceeded 120s."
    except Exception as e:
        return f"⚠️ SHELL_ERROR: {e}"


def _build_opencode_cmd(prompt: str, model: str = "") -> List[str]:
    """Build OpenCode command for non-interactive code edits."""
    cmd = [shutil.which("opencode") or "opencode", "run"]
    if model and str(model).strip():
        cmd.extend(["-m", str(model).strip()])
    cmd.extend([prompt, "--format", "json"])
    return cmd


def _run_opencode_cli(
    work_dir: str,
    prompt: str,
    env: dict,
    model: str = "",
    timeout_sec: int = 120,
) -> subprocess.CompletedProcess:
    """Run OpenCode in non-interactive mode and return subprocess result."""
    cmd = _build_opencode_cmd(prompt=prompt, model=model)
    return subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=max(15, int(timeout_sec)),
        env=env,
    )


def _opencode_has_error_payload(stdout: str) -> bool:
    """Return True when OpenCode returned an explicit JSON error payload."""
    text = (stdout or "").strip()
    if not text:
        return False
    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if isinstance(obj, dict) and str(obj.get("type", "")).strip().lower() == "error":
            return True
    return False


def _opencode_no_changes_detected(stdout: str, stderr: str = "") -> bool:
    txt = f"{stdout or ''}\n{stderr or ''}".lower()
    return "no changes to apply" in txt


def _is_copilot_reauth_error(stdout: str, stderr: str) -> bool:
    txt = f"{stdout or ''}\n{stderr or ''}".lower()
    return (
        "reauthenticate with the copilot provider" in txt
        or "api.githubcopilot.com/chat/completions" in txt
    )


def _opencode_fallback_models() -> List[str]:
    raw = os.environ.get("OUROBOROS_OPENCODE_FALLBACK_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    if not models:
        free_raw = os.environ.get("OUROBOROS_MODEL_FREE_LIST", "")
        models = [m.strip() for m in free_raw.split(",") if m.strip()] if free_raw else []
    # stable dedup preserving order
    dedup: List[str] = []
    seen = set()
    for m in models:
        if m in seen:
            continue
        seen.add(m)
        dedup.append(m)
    return dedup


def _opencode_prompt_limits() -> tuple[int, int]:
    max_chars = int(os.environ.get("OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", "3500") or "3500")
    max_lines = int(os.environ.get("OUROBOROS_OPENCODE_MAX_PROMPT_LINES", "120") or "120")
    return max(500, max_chars), max(20, max_lines)


def _opencode_prompt_too_large(prompt: str) -> tuple[bool, int, int, int, int]:
    char_count = len(prompt)
    line_count = prompt.count("\n") + 1
    max_chars, max_lines = _opencode_prompt_limits()
    too_large = char_count > max_chars or line_count > max_lines
    return too_large, char_count, line_count, max_chars, max_lines


def _extract_atomic_steps(prompt: str, max_items: int = 6) -> List[str]:
    # Try to keep only explicit checklist-style lines from long prompts.
    steps: List[str] = []
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("-", "•", "*")):
            cleaned = re.sub(r"^[-•*]\s*", "", line).strip()
        elif re.match(r"^\d+[.)]\s+", line):
            cleaned = re.sub(r"^\d+[.)]\s*", "", line).strip()
        else:
            continue
        if cleaned:
            steps.append(cleaned)
        if len(steps) >= max_items:
            return steps[:max_items]

    # Fallback when there are no bullet points.
    if not steps:
        for sentence in re.split(r"[.!?]\s+", prompt):
            s = sentence.strip()
            if len(s) >= 24:
                steps.append(s)
            if len(steps) >= max_items:
                break
    return steps[:max_items]


def _format_prompt_too_large_message(prompt: str) -> str:
    too_large, char_count, line_count, max_chars, max_lines = _opencode_prompt_too_large(prompt)
    if not too_large:
        return ""
    steps = _extract_atomic_steps(prompt)
    lines = [
        (
            "⚠️ OPENCODE_PROMPT_TOO_LARGE: "
            f"{char_count} chars, {line_count} lines "
            f"(limits: {max_chars} chars, {max_lines} lines)."
        ),
        "Split into smaller calls (1 file or 1 change per call) to avoid provider timeouts/internal errors.",
    ]
    if steps:
        lines.append("Suggested atomic steps:")
        for idx, step in enumerate(steps, start=1):
            lines.append(f"{idx}. {truncate_for_log(step, 220)}")
    lines.append(
        "Config: OUROBOROS_OPENCODE_MAX_PROMPT_CHARS, OUROBOROS_OPENCODE_MAX_PROMPT_LINES."
    )
    return "\n".join(lines)


def _append_tool_stats(ctx: ToolContext, payload: Dict[str, Any]) -> None:
    """Append tool execution metrics for observability."""
    try:
        append_jsonl(
            ctx.drive_logs() / "tools_stats.jsonl",
            {
                "ts": utc_now_iso(),
                "tool": "opencode_edit",
                **payload,
            },
        )
    except Exception:
        log.debug("Failed to append tools_stats.jsonl for opencode_edit", exc_info=True)


def _strip_wrapped_quotes(value: str) -> str:
    v = (value or "").strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_fast_edit_prompt(prompt: str) -> Dict[str, Any]:
    """Parse simple single-file replace instruction from prompt."""
    fields: Dict[str, str] = {}
    for raw_line in str(prompt or "").splitlines():
        m = re.match(r"^\s*(FILE|REPLACE|WITH|COUNT)\s*:\s*(.*?)\s*$", raw_line, re.IGNORECASE)
        if not m:
            continue
        key = m.group(1).lower()
        val = _strip_wrapped_quotes(m.group(2))
        fields[key] = val

    if {"file", "replace", "with"} <= set(fields.keys()):
        count_raw = fields.get("count", "1").strip().lower()
        if count_raw in {"all", "*"}:
            count = 0
        else:
            try:
                count = max(1, int(count_raw))
            except Exception:
                count = 1
        return {
            "file": fields["file"].strip(),
            "replace": fields["replace"],
            "with": fields["with"],
            "count": count,
            "reason": "structured_file_replace",
        }

    # Lightweight natural-language fallback for one-line edits.
    patterns = [
        r"""replace\s+["'](?P<old>.+?)["']\s+with\s+["'](?P<new>.+?)["']\s+in\s+file\s+["'](?P<file>.+?)["']""",
        r"""замени\s+["'](?P<old>.+?)["']\s+на\s+["'](?P<new>.+?)["']\s+в\s+файле\s+["'](?P<file>.+?)["']""",
    ]
    text = str(prompt or "")
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return {
                "file": m.group("file").strip(),
                "replace": m.group("old"),
                "with": m.group("new"),
                "count": 1,
                "reason": "natural_language_single_replace",
            }
    return {}


def _resolve_fast_edit_target(repo_dir: pathlib.Path, work_dir: str, file_rel: str) -> pathlib.Path:
    base = pathlib.Path(work_dir).resolve()
    candidate = (base / file_rel).resolve()
    repo_root = repo_dir.resolve()
    try:
        candidate.relative_to(repo_root)
    except Exception as e:
        raise ValueError(f"target outside repo: {file_rel}") from e
    return candidate


def _apply_fast_edit(ctx: ToolContext, work_dir: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a simple replace operation via patch subprocess (with safe fallback)."""
    target = _resolve_fast_edit_target(ctx.repo_dir, work_dir, str(plan.get("file") or ""))
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": f"target file not found: {target}"}

    old = str(plan.get("replace") or "")
    new = str(plan.get("with") or "")
    count = int(plan.get("count") or 1)
    if not old:
        return {"ok": False, "error": "REPLACE text is empty"}
    if "\n" in old or "\n" in new:
        return {"ok": False, "error": "fast edit supports only single-line replace"}

    original = target.read_text(encoding="utf-8")
    occurrences = original.count(old)
    if occurrences <= 0:
        return {"ok": False, "error": "replace text not found in target file"}

    replace_count = occurrences if count <= 0 else min(count, occurrences)
    if count <= 0:
        updated = original.replace(old, new)
    else:
        updated = original.replace(old, new, replace_count)
    if updated == original:
        return {"ok": False, "error": "no effective changes produced"}

    rel = os.path.relpath(target, start=work_dir)
    patch_text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=rel,
            tofile=rel,
        )
    )
    if not patch_text.strip():
        return {"ok": False, "error": "empty patch generated"}

    method = "patch"
    try:
        patch_res = subprocess.run(
            ["patch", "-p0", "--forward", "--silent"],
            cwd=str(work_dir),
            input=patch_text,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if patch_res.returncode != 0:
            method = "python_replace_fallback"
            target.write_text(updated, encoding="utf-8")
    except Exception:
        method = "python_replace_fallback"
        target.write_text(updated, encoding="utf-8")

    return {
        "ok": True,
        "method": method,
        "file": rel,
        "replacements_applied": replace_count,
    }


def _check_uncommitted_changes(repo_dir: pathlib.Path, source: str = "OpenCode") -> str:
    """Check git status after edit, return warning string or empty string."""
    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status_res.returncode == 0 and status_res.stdout.strip():
            diff_res = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if diff_res.returncode == 0 and diff_res.stdout.strip():
                return (
                    f"\n\n⚠️ UNCOMMITTED CHANGES detected after {source} edit:\n"
                    f"{diff_res.stdout.strip()}\n"
                    f"Remember to run git_status and repo_commit_push!"
                )
    except Exception as e:
        log.debug("Failed to check git status after opencode_edit: %s", e, exc_info=True)
    return ""


def _parse_opencode_output(stdout: str) -> str:
    """Parse OpenCode output (JSON or JSONL) and extract a readable text result."""
    text = (stdout or "").strip()
    if not text:
        return ""

    def _extract_text_from_obj(obj: Any) -> List[str]:
        out: List[str] = []
        if isinstance(obj, dict):
            for key in ("text", "result", "output", "message", "content"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
            if "delta" in obj and isinstance(obj["delta"], str) and obj["delta"].strip():
                out.append(obj["delta"].strip())
        elif isinstance(obj, list):
            for item in obj:
                out.extend(_extract_text_from_obj(item))
        return out

    # Try single JSON payload first
    try:
        payload = json.loads(text)
        parts = _extract_text_from_obj(payload)
        if parts:
            return "\n".join(parts)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Fallback: parse newline-delimited JSON events
    collected: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        collected.extend(_extract_text_from_obj(obj))
    if collected:
        return "\n".join(collected)
    return text


def _emit_opencode_usage_if_available(ctx: ToolContext, stdout: str) -> None:
    """Emit llm_usage event when OpenCode returns numeric cost in JSON payload."""
    try:
        payload = json.loads((stdout or "").strip())
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    cost = payload.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        cost = payload.get("cost")
    if not isinstance(cost, (int, float)):
        return

    ctx.pending_events.append({
        "type": "llm_usage",
        "provider": "opencode_cli",
        "usage": {"cost": float(cost)},
        "source": "opencode_edit",
        "ts": utc_now_iso(),
        "category": "task",
    })


def _run_pytest(repo_dir: pathlib.Path) -> str:
    """Run pytest -q --tb=short after an edit; returns summary or empty string if pytest unavailable."""
    if not shutil.which("pytest"):
        return ""
    try:
        res = subprocess.run(
            ["pytest", "-q", "--tb=short"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (res.stdout + ("\n" + res.stderr if res.stderr.strip() else "")).strip()
        if res.returncode == 0:
            return f"\n\n--- pytest ---\n{output}"
        else:
            return f"\n\n⚠️ PYTEST FAILED (exit={res.returncode}):\n{output}"
    except subprocess.TimeoutExpired:
        return "\n\n⚠️ PYTEST TIMEOUT: exceeded 120s."
    except Exception as e:
        log.debug("Failed to run pytest after code edit: %s", e, exc_info=True)
        return ""


def _opencode_edit(ctx: ToolContext, prompt: str, cwd: str = "") -> str:
    """Edit code via adaptive route: fast local patch for simple fixes, otherwise OpenCode CLI."""
    started = time.monotonic()

    def _finish(
        result: str,
        *,
        ok: bool,
        route: str,
        fallback_used: bool,
        attempts_total: int,
        models_tried: List[str],
        budget_exhausted: bool,
        failure_reason: str = "",
        fast_edit_reason: str = "",
        fast_edit_method: str = "",
        fast_edit_file: str = "",
        fast_edit_replacements: int = 0,
    ) -> str:
        _append_tool_stats(
            ctx,
            {
                "ok": bool(ok),
                "route": route,
                "fallback_used": bool(fallback_used),
                "attempts_total": int(max(0, attempts_total)),
                "retries_used": int(max(0, attempts_total - 1)),
                "models_tried": models_tried,
                "budget_exhausted": bool(budget_exhausted),
                "failure_reason": failure_reason,
                "fast_edit_reason": fast_edit_reason,
                "fast_edit_method": fast_edit_method,
                "fast_edit_file": fast_edit_file,
                "fast_edit_replacements": int(max(0, fast_edit_replacements)),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return result

    if not isinstance(prompt, str) or not prompt.strip():
        return _finish(
            "⚠️ OPENCODE_ARG_ERROR: prompt must be a non-empty string.",
            ok=False,
            route="reject",
            fallback_used=False,
            attempts_total=0,
            models_tried=[],
            budget_exhausted=False,
            failure_reason="arg_error",
        )
    too_large_msg = _format_prompt_too_large_message(prompt)
    if too_large_msg:
        return _finish(
            too_large_msg,
            ok=False,
            route="reject",
            fallback_used=False,
            attempts_total=0,
            models_tried=[],
            budget_exhausted=False,
            failure_reason="prompt_too_large",
        )
    from ouroboros.tools.git import _acquire_git_lock, _release_git_lock

    work_dir = str(ctx.repo_dir)
    if cwd and cwd.strip() not in ("", ".", "./"):
        candidate = (ctx.repo_dir / cwd).resolve()
        if candidate.exists():
            work_dir = str(candidate)

    lock = _acquire_git_lock(ctx)
    try:
        try:
            run_cmd(["git", "checkout", ctx.branch_dev], cwd=ctx.repo_dir)
        except Exception as e:
            return _finish(
                f"⚠️ GIT_ERROR (checkout): {e}",
                ok=False,
                route="reject",
                fallback_used=False,
                attempts_total=0,
                models_tried=[],
                budget_exhausted=False,
                failure_reason="git_checkout_error",
            )

        fast_plan = _parse_fast_edit_prompt(prompt)
        route = "opencode"
        fallback_used = False
        fast_edit_reason = str(fast_plan.get("reason") or "") if fast_plan else ""
        if fast_plan:
            ctx.emit_progress_fn("Applying fast local edit...")
            fast = _apply_fast_edit(ctx, work_dir=work_dir, plan=fast_plan)
            if fast.get("ok"):
                out = (
                    "✅ FAST_EDIT_APPLIED: "
                    f"{fast.get('file')} "
                    f"(replacements={int(fast.get('replacements_applied') or 0)}, method={fast.get('method')})"
                )
                warning = _check_uncommitted_changes(ctx.repo_dir, source="fast-path")
                if warning:
                    out += warning
                out += _run_pytest(ctx.repo_dir)
                return _finish(
                    out,
                    ok=True,
                    route="fast_path",
                    fallback_used=False,
                    attempts_total=0,
                    models_tried=[],
                    budget_exhausted=False,
                    fast_edit_reason=fast_edit_reason,
                    fast_edit_method=str(fast.get("method") or ""),
                    fast_edit_file=str(fast.get("file") or ""),
                    fast_edit_replacements=int(fast.get("replacements_applied") or 0),
                )
            route = "fallback_to_opencode"
            fallback_used = True
            ctx.emit_progress_fn("Fast local edit failed, delegating to OpenCode CLI...")
        else:
            ctx.emit_progress_fn("Delegating to OpenCode CLI...")

        full_prompt = (
            f"STRICT: Only modify files inside {work_dir}. "
            f"Git branch: {ctx.branch_dev}. Do NOT commit or push.\n\n"
            f"{prompt}"
        )

        env = os.environ.copy()
        local_bin = str(pathlib.Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        max_retries = max(1, int(os.environ.get("OUROBOROS_OPENCODE_MAX_RETRIES", "1") or "1"))
        # Keep this below tool timeout_sec (300) to guarantee lock release in finally.
        total_budget_sec = max(60, int(os.environ.get("OUROBOROS_OPENCODE_TOTAL_BUDGET_SEC", "260") or "260"))
        per_attempt_timeout_sec = max(20, int(os.environ.get("OUROBOROS_OPENCODE_ATTEMPT_TIMEOUT_SEC", "90") or "90"))
        deadline = time.monotonic() + total_budget_sec
        model_attempts: List[str] = [""] + _opencode_fallback_models()
        res = None
        stdout = ""
        stderr = ""
        failed = True
        attempt_notes: List[str] = []
        budget_exhausted = False
        attempts_total = 0
        models_tried: List[str] = []

        for model in model_attempts:
            model_label = model or "default"
            for attempt in range(1, max_retries + 1):
                remaining = int(deadline - time.monotonic())
                if remaining <= 15:
                    budget_exhausted = True
                    attempt_notes.append(f"{model_label}#{attempt}:budget_exhausted")
                    break
                effective_timeout = min(per_attempt_timeout_sec, max(15, remaining - 5))
                attempts_total += 1
                if model_label not in models_tried:
                    models_tried.append(model_label)
                if model:
                    ctx.emit_progress_fn(
                        f"OpenCode retry with {model_label} (attempt {attempt}/{max_retries})..."
                    )
                try:
                    cur_res = _run_opencode_cli(
                        work_dir=work_dir,
                        prompt=full_prompt,
                        env=env,
                        model=model,
                        timeout_sec=effective_timeout,
                    )
                    cur_stdout = (cur_res.stdout or "").strip()
                    cur_stderr = (cur_res.stderr or "").strip()
                    cur_failed = (
                        cur_res.returncode != 0
                        or _opencode_has_error_payload(cur_stdout)
                    )
                    if not cur_failed:
                        res = cur_res
                        stdout = cur_stdout
                        stderr = cur_stderr
                        failed = False
                        break

                    if _opencode_no_changes_detected(cur_stdout, cur_stderr):
                        return "ℹ️ OpenCode: no changes to apply (target state already reached)."

                    reason = "copilot_auth" if _is_copilot_reauth_error(cur_stdout, cur_stderr) else "tool_or_provider_error"
                    attempt_notes.append(
                        f"{model_label}#{attempt}:{reason}:exit={cur_res.returncode}:t={effective_timeout}s"
                    )
                    stdout = cur_stdout
                    stderr = cur_stderr
                    res = cur_res
                except subprocess.TimeoutExpired as te:
                    timeout_stdout = (te.stdout or "").strip() if isinstance(te.stdout, str) else ""
                    timeout_stderr = (te.stderr or "").strip() if isinstance(te.stderr, str) else ""
                    if _opencode_no_changes_detected(timeout_stdout, timeout_stderr):
                        return "ℹ️ OpenCode: no changes to apply (target state already reached)."
                    attempt_notes.append(f"{model_label}#{attempt}:timeout:{effective_timeout}s")
                    stdout = timeout_stdout
                    stderr = timeout_stderr
                    res = subprocess.CompletedProcess(
                        args=[],
                        returncode=124,
                        stdout=timeout_stdout,
                        stderr=timeout_stderr,
                    )
                except Exception as e:
                    attempt_notes.append(f"{model_label}#{attempt}:{type(e).__name__}")
                    stdout = ""
                    stderr = str(e)
                    res = subprocess.CompletedProcess(
                        args=[],
                        returncode=125,
                        stdout="",
                        stderr=str(e),
                    )

            if not failed:
                break
            if budget_exhausted:
                break

        if failed:
            budget_note = ""
            if budget_exhausted:
                budget_note = (
                    f"\nBudget limit reached: OUROBOROS_OPENCODE_TOTAL_BUDGET_SEC={total_budget_sec}, "
                    f"OUROBOROS_OPENCODE_ATTEMPT_TIMEOUT_SEC={per_attempt_timeout_sec}."
                )
            help_text = (
                "\n\nTroubleshooting:\n"
                "1) Ensure /app/opencode.json exists with provider 'opencode' and free model defaults.\n"
                "2) Verify key is present in container: OPENCODE_API_KEY.\n"
                "3) Test manually: opencode run -m opencode/minimax-m2.5-free \"Reply with exactly: OK\" --format json\n"
            )
            attempts_text = ", ".join(attempt_notes[-10:]) if attempt_notes else "n/a"
            return _finish(
                f"⚠️ OPENCODE_ERROR: exit={res.returncode if res is not None else 'n/a'}\n"
                f"Attempts: {attempts_text}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                f"{budget_note}{help_text}",
                ok=False,
                route=route,
                fallback_used=fallback_used,
                attempts_total=attempts_total,
                models_tried=models_tried,
                budget_exhausted=budget_exhausted,
                failure_reason="opencode_failed",
                fast_edit_reason=fast_edit_reason,
            )
        if not stdout:
            stdout = "OK: OpenCode completed with empty output."

        warning = _check_uncommitted_changes(ctx.repo_dir, source="OpenCode")
        if warning:
            stdout += warning
        _emit_opencode_usage_if_available(ctx, stdout)

    except Exception as e:
        return _finish(
            f"⚠️ OPENCODE_FAILED: {type(e).__name__}: {e}",
            ok=False,
            route="opencode",
            fallback_used=False,
            attempts_total=0,
            models_tried=[],
            budget_exhausted=False,
            failure_reason="unexpected_exception",
        )
    finally:
        _release_git_lock(lock)

    result = _parse_opencode_output(stdout)
    result += _run_pytest(ctx.repo_dir)
    return _finish(
        result,
        ok=True,
        route=route,
        fallback_used=fallback_used,
        attempts_total=attempts_total,
        models_tried=models_tried,
        budget_exhausted=budget_exhausted,
        fast_edit_reason=fast_edit_reason,
    )


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("run_shell", {
            "name": "run_shell",
            "description": "Run a shell command (list of args) inside the repo. Returns stdout+stderr.",
            "parameters": {"type": "object", "properties": {
                "cmd": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string", "default": ""},
            }, "required": ["cmd"]},
        }, _run_shell, is_code_tool=True),
        ToolEntry("opencode_edit", {
            "name": "opencode_edit",
            "description": "Delegate code edits to OpenCode CLI. The sole way to edit code. Follow with repo_commit_push.",
            "parameters": {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string", "default": ""},
            }, "required": ["prompt"]},
        }, _opencode_edit, is_code_tool=True, timeout_sec=300),
    ]
