"""Shell tools: run_shell, patch_edit."""

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
import uuid
from urllib.parse import urlparse, urlunparse
from typing import Any, Dict, List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import append_jsonl, read_text, run_cmd, safe_resolve_under_root, truncate_for_log, utc_now_iso, write_text

log = logging.getLogger(__name__)


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(min_value, parsed)


def _run_shell_timeout_sec() -> int:
    return _env_int("OUROBOROS_RUN_SHELL_TIMEOUT_SEC", 120, min_value=1)


def _run_shell_output_cap_bytes() -> int:
    return _env_int("OUROBOROS_RUN_SHELL_MAX_OUTPUT_BYTES", 100_000, min_value=4096)


def _truncate_output_bytes(text: str, limit_bytes: int) -> str:
    if limit_bytes <= 0:
        return ""
    data = (text or "").encode("utf-8", errors="replace")
    if len(data) <= limit_bytes:
        return text or ""
    half = max(64, limit_bytes // 2)
    head = data[:half].decode("utf-8", errors="ignore")
    tail = data[-half:].decode("utf-8", errors="ignore")
    return head + f"\n...(truncated at {limit_bytes} bytes)...\n" + tail


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return default


def _is_opencode_cli_invocation(cmd: List[str]) -> bool:
    if not cmd:
        return False
    first = pathlib.Path(str(cmd[0] or "").strip()).name.lower()
    if first in {"opencode", "opencode.exe", "codex", "codex.exe"}:
        return True
    if first in {"bash", "sh", "zsh"} and len(cmd) >= 3:
        script = str(cmd[2] or "").lower()
        if (
            "opencode " in script
            or script.strip().startswith("opencode")
            or "codex " in script
            or script.strip().startswith("codex")
        ):
            return True
    if first in {"npx", "npm", "pnpm", "yarn", "bun"} and len(cmd) >= 2:
        second = pathlib.Path(str(cmd[1] or "").strip()).name.lower()
        if second in {"opencode", "@opencode/cli", "codex", "@openai/codex"}:
            return True
    return False


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

    if _is_opencode_cli_invocation(cmd):
        msg = (
            "⚠️ RUN_SHELL_POLICY: Не запускай OpenCode/Codex CLI через run_shell. "
            "Для правок кода используй tool patch_edit; run_shell оставь для проверок/тестов."
        )
        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "tool_warning",
                "tool": "run_shell",
                "warning": "run_shell_blocked_nested_opencode_cli",
                "cmd_preview": truncate_for_log(" ".join(cmd), 500),
            })
        except Exception:
            log.debug("Failed to log blocked nested opencode invocation", exc_info=True)
        return msg

    timeout_sec = _run_shell_timeout_sec()
    output_cap_bytes = _run_shell_output_cap_bytes()

    work_dir = ctx.repo_dir.resolve()
    if cwd and cwd.strip() not in ("", ".", "./"):
        try:
            candidate = safe_resolve_under_root(ctx.repo_dir, cwd)
        except ValueError as e:
            return f"⚠️ PATH_ERROR: {e}"
        if candidate.exists() and candidate.is_dir():
            work_dir = candidate

    try:
        res = subprocess.run(
            cmd, cwd=str(work_dir),
            capture_output=True, text=True, timeout=timeout_sec,
        )
        out = (res.stdout or "") + ("\n--- STDERR ---\n" + (res.stderr or "") if res.stderr else "")
        out = _truncate_output_bytes(out, output_cap_bytes)
        prefix = f"exit_code={res.returncode}\n"
        return prefix + out
    except subprocess.TimeoutExpired:
        return f"⚠️ TIMEOUT: command exceeded {timeout_sec}s."
    except Exception as e:
        return f"⚠️ SHELL_ERROR: {e}"


def _codex_cli_base_url() -> str:
    explicit = _env_first("OUROBOROS_CODEX_CLI_BASE_URL", default="")
    if explicit:
        return explicit

    llm_base = _env_first("OUROBOROS_LLM_BASE_URL", default="")
    if not llm_base:
        return ""

    parsed = urlparse(llm_base)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/backend-api/codex"):
        new_path = path
    else:
        if path.endswith("/v1"):
            path = path[:-3]
        new_path = (path.rstrip("/") + "/backend-api/codex") or "/backend-api/codex"
    return urlunparse(parsed._replace(path=new_path, params="", query="", fragment=""))


def _codex_cli_model(default: str = "") -> str:
    return (
        str(default or "").strip()
        or _env_first("OUROBOROS_MODEL_CODE", "OUROBOROS_MODEL", default="gpt-5.4")
    )


def _build_opencode_cmd(
    prompt: str,
    model: str = "",
    session_id: str = "",
    work_dir: str = "",
) -> List[str]:
    """Build Codex CLI command for non-interactive code edits."""
    cmd = [shutil.which("codex") or "codex"]
    if str(work_dir or "").strip():
        cmd.extend(["-C", str(work_dir).strip()])
    cmd.append("exec")
    if session_id:
        cmd.extend(["resume", "--json"])
    else:
        cmd.append("--json")
    resolved_model = _codex_cli_model(model)
    if resolved_model:
        cmd.extend(["-m", resolved_model])
    cmd.extend(["--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"])
    if session_id:
        cmd.append(str(session_id).strip())
    cmd.append(prompt)
    return cmd


def _run_opencode_cli(
    work_dir: str,
    prompt: str,
    env: dict,
    model: str = "",
    session_id: str = "",
    timeout_sec: int = 120,
) -> subprocess.CompletedProcess:
    """Run Codex CLI in non-interactive mode and return subprocess result."""
    cmd = _build_opencode_cmd(
        prompt=prompt,
        model=model,
        session_id=session_id,
        work_dir=work_dir,
    )
    prompt_arg = cmd.pop()
    provider_name = "ouroboros"
    base_url = _codex_cli_base_url()
    if base_url:
        cmd.extend([
            "-c", f"model_provider={provider_name}",
            "-c", f"model_providers.{provider_name}.name={provider_name}",
            "-c", f"model_providers.{provider_name}.base_url={base_url}",
            "-c", f"model_providers.{provider_name}.env_key=OPENAI_API_KEY",
        ])
    cmd.append(prompt_arg)
    return subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=max(15, int(timeout_sec)),
        env=env,
    )


def _codex_home_dir(ctx: ToolContext) -> pathlib.Path:
    return (ctx.drive_root / "codex_home").resolve()


def _codex_task_session_file(ctx: ToolContext) -> Optional[pathlib.Path]:
    task_id = str(getattr(ctx, "task_id", "") or "").strip()
    if not task_id:
        return None
    return (ctx.drive_root / "state" / "codex_task_sessions" / f"{task_id}.json").resolve()


def _load_codex_task_session(ctx: ToolContext) -> Dict[str, Any]:
    path = _codex_task_session_file(ctx)
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(read_text(path))
    except Exception:
        log.debug("Failed to read codex task session file %s", path, exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_codex_task_session(ctx: ToolContext, payload: Dict[str, Any]) -> None:
    path = _codex_task_session_file(ctx)
    if path is None:
        return
    current = _load_codex_task_session(ctx)
    merged: Dict[str, Any] = {}
    if isinstance(current, dict):
        merged.update(current)
    merged.update(payload)
    merged["task_id"] = str(getattr(ctx, "task_id", "") or "").strip()
    merged["updated_at"] = utc_now_iso()
    write_text(path, json.dumps(merged, ensure_ascii=False, indent=2))


def _resolve_opencode_requested_work_dir(ctx: ToolContext, cwd: str = "") -> str:
    work_dir = str(ctx.repo_dir.resolve())
    raw_cwd = str(cwd or "").strip()
    if raw_cwd in {"", ".", "./"}:
        return work_dir
    try:
        candidate = safe_resolve_under_root(ctx.repo_dir, raw_cwd)
    except ValueError:
        return work_dir
    if candidate.exists() and candidate.is_dir():
        return str(candidate.resolve())
    return work_dir


def _resolve_opencode_session_work_dir(ctx: ToolContext, session_state: Dict[str, Any]) -> str:
    stored = str((session_state or {}).get("work_dir") or "").strip()
    if not stored:
        return ""
    root_resolved = ctx.repo_dir.resolve()
    try:
        raw_path = pathlib.Path(stored)
        if raw_path.is_absolute():
            candidate = raw_path.resolve()
            if candidate != root_resolved and root_resolved not in candidate.parents:
                return ""
        else:
            candidate = safe_resolve_under_root(ctx.repo_dir, stored)
    except ValueError:
        return ""
    if candidate.exists() and candidate.is_dir():
        return str(candidate.resolve())
    return ""


def _choose_opencode_work_dir(
    ctx: ToolContext,
    *,
    cwd: str = "",
    session_state: Optional[Dict[str, Any]] = None,
) -> str:
    requested_work_dir = _resolve_opencode_requested_work_dir(ctx, cwd=cwd)
    state = session_state if isinstance(session_state, dict) else {}
    active_session_id = str(state.get("current_session_id") or "").strip()
    session_work_dir = _resolve_opencode_session_work_dir(ctx, state)
    if active_session_id and session_work_dir:
        return session_work_dir
    return requested_work_dir


def _extract_opencode_thread_id(stdout: str) -> str:
    text = (stdout or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("type") or "").strip() == "thread.started":
            thread_id = str(obj.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
    try:
        payload = json.loads(text)
    except Exception:
        return ""
    if isinstance(payload, dict) and str(payload.get("type") or "").strip() == "thread.started":
        return str(payload.get("thread_id") or "").strip()
    return ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


def _self_edit_only_enabled() -> bool:
    return _env_bool("OUROBOROS_SELF_EDIT_ONLY", default=True)


def _code_edit_auto_install_enabled() -> bool:
    raw = _env_first("OUROBOROS_CODEX_AUTO_INSTALL", "OUROBOROS_OPENCODE_AUTO_INSTALL", default="")
    if not raw:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _append_tool_event(ctx: ToolContext, payload: Dict[str, Any]) -> None:
    try:
        append_jsonl(
            ctx.drive_logs() / "events.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "opencode_bootstrap",
                **payload,
            },
        )
    except Exception:
        log.debug("Failed to append opencode bootstrap event", exc_info=True)


def _prepend_path_entries(path_value: str, entries: List[str]) -> str:
    ordered: List[str] = []
    seen = set()
    for item in entries + (path_value or "").split(":"):
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    return ":".join(ordered)


def _ensure_opencode_cli(
    ctx: ToolContext,
    *,
    work_dir: str,
    env: Dict[str, str],
    timeout_sec: int = 150,
) -> tuple[bool, str]:
    """Best-effort bootstrap for Codex CLI inside runtime container."""
    codex_path = shutil.which("codex", path=env.get("PATH", ""))
    if codex_path:
        return True, codex_path

    if not _code_edit_auto_install_enabled():
        msg = (
            "codex not found in PATH and auto-install is disabled "
            "(OUROBOROS_CODEX_AUTO_INSTALL=0)."
        )
        _append_tool_event(ctx, {"ok": False, "stage": "disabled", "error": msg})
        return False, msg

    install_cmd = (
        _env_first("OUROBOROS_CODEX_INSTALL_CMD", "OUROBOROS_OPENCODE_INSTALL_CMD", default="")
        or "npm install -g @openai/codex"
    )
    ctx.emit_progress_fn("Codex CLI не найден, пробую автоустановку...")
    try:
        install_res = subprocess.run(
            ["bash", "-lc", install_cmd],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired:
        msg = f"Codex CLI install timed out after {timeout_sec}s."
        _append_tool_event(ctx, {"ok": False, "stage": "install", "error": msg})
        return False, msg
    except Exception as e:
        msg = f"Codex CLI install failed: {type(e).__name__}: {e}"
        _append_tool_event(ctx, {"ok": False, "stage": "install", "error": msg})
        return False, msg

    if install_res.returncode != 0:
        stdout = truncate_for_log((install_res.stdout or "").strip(), 1200)
        stderr = truncate_for_log((install_res.stderr or "").strip(), 1200)
        msg = (
            f"Codex CLI install exited {install_res.returncode}. "
            f"stdout={stdout or '-'} stderr={stderr or '-'}"
        )
        _append_tool_event(
            ctx,
            {
                "ok": False,
                "stage": "install",
                "error": msg,
                "returncode": int(install_res.returncode),
            },
        )
        return False, msg

    codex_path = shutil.which("codex", path=env.get("PATH", ""))
    if not codex_path:
        msg = "Codex CLI install completed but binary still missing in PATH."
        _append_tool_event(ctx, {"ok": False, "stage": "which", "error": msg})
        return False, msg

    try:
        version_res = subprocess.run(
            [codex_path, "--version"],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if version_res.returncode != 0:
            msg = (
                f"Codex CLI installed at {codex_path}, "
                f"but --version failed (exit={version_res.returncode})."
            )
            _append_tool_event(
                ctx,
                {
                    "ok": False,
                    "stage": "verify",
                    "error": msg,
                    "returncode": int(version_res.returncode),
                },
            )
            return False, msg
    except Exception as e:
        msg = f"Codex CLI verify failed: {type(e).__name__}: {e}"
        _append_tool_event(ctx, {"ok": False, "stage": "verify", "error": msg})
        return False, msg

    _append_tool_event(ctx, {"ok": True, "stage": "ready", "path": codex_path})
    return True, codex_path


def _opencode_has_error_payload(stdout: str) -> bool:
    """Return True when code-edit CLI returned an explicit JSON error payload."""
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
    raw = _env_first("OUROBOROS_CODEX_FALLBACK_MODELS", "OUROBOROS_OPENCODE_FALLBACK_MODELS", default="")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
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
    max_chars = int(
        _env_first("OUROBOROS_CODEX_MAX_PROMPT_CHARS", "OUROBOROS_OPENCODE_MAX_PROMPT_CHARS", default="12000")
        or "12000"
    )
    max_lines = int(
        _env_first("OUROBOROS_CODEX_MAX_PROMPT_LINES", "OUROBOROS_OPENCODE_MAX_PROMPT_LINES", default="300")
        or "300"
    )
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
        "Config: OUROBOROS_CODEX_MAX_PROMPT_CHARS, OUROBOROS_CODEX_MAX_PROMPT_LINES."
    )
    return "\n".join(lines)


def _append_tool_stats(ctx: ToolContext, payload: Dict[str, Any], tool_name: str = "patch_edit") -> None:
    """Append tool execution metrics for observability."""
    try:
        append_jsonl(
            ctx.drive_logs() / "tools_stats.jsonl",
            {
                "ts": utc_now_iso(),
                "tool": tool_name,
                **payload,
            },
        )
    except Exception:
        log.debug("Failed to append tools_stats.jsonl for %s", tool_name, exc_info=True)


def _strip_wrapped_quotes(value: str) -> str:
    v = (value or "").strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_fast_edit_prompt(prompt: str) -> Dict[str, Any]:
    """Parse simple single-file replace instruction from prompt."""
    fields: Dict[str, str] = {}
    current_key = ""
    current_lines: List[str] = []

    def _flush() -> None:
        nonlocal current_key, current_lines
        if not current_key:
            return
        value = "\n".join(current_lines).rstrip("\n").strip()
        fields[current_key] = _strip_wrapped_quotes(value)
        current_key = ""
        current_lines = []

    for raw_line in str(prompt or "").splitlines():
        m = re.match(r"^\s*(FILE|REPLACE|WITH|COUNT)\s*:\s*(.*?)\s*$", raw_line, re.IGNORECASE)
        if m:
            _flush()
            current_key = m.group(1).lower()
            initial_value = m.group(2)
            current_lines = [initial_value] if initial_value else []
            continue
        if current_key:
            current_lines.append(raw_line)
    _flush()

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


def _is_heavy_opencode_prompt(prompt: str) -> tuple[bool, str]:
    text = str(prompt or "")
    lowered = text.lower()
    char_count = len(text)
    line_count = text.count("\n") + 1

    max_chars = _env_int(
        "OUROBOROS_CODEX_DIRECT_HEAVY_CHARS",
        _env_int("OUROBOROS_OPENCODE_DIRECT_HEAVY_CHARS", 900, min_value=200),
        min_value=200,
    )
    max_lines = _env_int(
        "OUROBOROS_CODEX_DIRECT_HEAVY_LINES",
        _env_int("OUROBOROS_OPENCODE_DIRECT_HEAVY_LINES", 28, min_value=5),
        min_value=5,
    )

    if char_count > max_chars:
        return True, f"chars>{max_chars}"
    if line_count > max_lines:
        return True, f"lines>{max_lines}"

    default_keywords = (
        "refactor",
        "рефактор",
        "декомпоз",
        "архитект",
        "audit",
        "ревью",
        "review",
        "deep research",
        "поиск баг",
        "diagnos",
        "диагност",
        "entire project",
        "whole project",
        "full project",
    )
    for kw in default_keywords:
        if kw in lowered:
            return True, f"keyword:{kw}"

    return False, ""


def _offload_patch_edit_to_worker(
    ctx: ToolContext,
    prompt: str,
    cwd: str,
    reason: str,
) -> str:
    task_id = uuid.uuid4().hex[:8]
    prompt_preview = truncate_for_log(prompt.replace("\n", " "), 140)
    description = f"Выполни правку кода через patch_edit: {prompt_preview}"
    parent_task_id = str(getattr(ctx, "task_id", "") or "").strip()
    context_lines = [
        "Авто-маршрутизация тяжёлой правки из direct chat в worker.",
        f"Причина: {reason}",
        f"CWD hint: {cwd or '.'}",
        "Сделай 1 вызов patch_edit с исходным prompt ниже.",
        "После правки проверь изменения и дай краткий отчёт на русском.",
        "",
        "[BEGIN_PATCH_EDIT_PROMPT]",
        prompt,
        "[END_PATCH_EDIT_PROMPT]",
    ]

    evt: Dict[str, Any] = {
        "type": "schedule_task",
        "task_id": task_id,
        "description": description,
        "context": "\n".join(context_lines),
        "ts": utc_now_iso(),
    }
    if parent_task_id:
        evt["parent_task_id"] = parent_task_id
    ctx.pending_events.append(evt)

    try:
        append_jsonl(
            ctx.drive_logs() / "events.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "patch_edit_offloaded_to_worker",
                "task_id": parent_task_id,
                "scheduled_task_id": task_id,
                "reason": reason,
                "prompt_preview": truncate_for_log(prompt, 400),
            },
        )
    except Exception:
        log.debug("Failed to log patch_edit offload event", exc_info=True)

    return (
        f"↪️ HEAVY_PATCH_EDIT_OFFLOADED: scheduled task {task_id}. "
        "Use wait_for_task with this id to track completion."
    )


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


def _check_uncommitted_changes(repo_dir: pathlib.Path, source: str = "Codex CLI") -> str:
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
        log.debug("Failed to check git status after code edit: %s", e, exc_info=True)
    return ""


def _patch_edit_format_message(error: str = "") -> str:
    base = (
        "⚠️ PATCH_EDIT_FORMAT: use one structured self-edit prompt in the form:\n"
        "FILE: path/to/file\n"
        "REPLACE: old text\n"
        "WITH: new text\n"
        "COUNT: 1"
    )
    if error:
        return f"{base}\nReason: {error}"
    return base


def _parse_opencode_output(stdout: str) -> str:
    """Parse code-edit CLI output (JSON or JSONL) and extract a readable text result."""
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
            for val in obj.values():
                if isinstance(val, (dict, list)):
                    out.extend(_extract_text_from_obj(val))
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
    """Emit llm_usage event when code-edit CLI returns usage in JSON payload."""
    text = (stdout or "").strip()
    if not text:
        return

    usage_payload: Dict[str, Any] | None = None
    cost: float | None = None

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            maybe_cost = payload.get("total_cost_usd")
            if not isinstance(maybe_cost, (int, float)):
                maybe_cost = payload.get("cost")
            if isinstance(maybe_cost, (int, float)):
                cost = float(maybe_cost)
            maybe_usage = payload.get("usage")
            if isinstance(maybe_usage, dict):
                usage_payload = maybe_usage
    except Exception:
        pass

    if usage_payload is None:
        for line in text.splitlines():
            ln = line.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            maybe_usage = obj.get("usage")
            if isinstance(maybe_usage, dict):
                usage_payload = maybe_usage
            maybe_cost = obj.get("total_cost_usd")
            if not isinstance(maybe_cost, (int, float)):
                maybe_cost = obj.get("cost")
            if isinstance(maybe_cost, (int, float)):
                cost = float(maybe_cost)

    if cost is None and not usage_payload:
        return

    usage_event: Dict[str, Any] = {}
    if usage_payload:
        usage_event.update(usage_payload)
    if cost is not None:
        usage_event["cost"] = float(cost)

    ctx.pending_events.append({
        "type": "llm_usage",
        "provider": "codex_cli",
        "usage": usage_event,
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


def _patch_edit(ctx: ToolContext, prompt: str, cwd: str = "") -> str:
    """Edit code via local structured self-edit patches only."""
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
            "⚠️ CODE_EDIT_ARG_ERROR: prompt must be a non-empty string.",
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

    from ouroboros.tools.git import _acquire_git_lock, _ensure_git_repo_ready, _release_git_lock

    session_state = _load_codex_task_session(ctx)
    work_dir = _choose_opencode_work_dir(ctx, cwd=cwd, session_state=session_state)

    lock = _acquire_git_lock(ctx)
    try:
        ok, repo_note = _ensure_git_repo_ready(ctx, action="patch_edit", auto_recover=True)
        if not ok:
            return _finish(
                repo_note,
                ok=False,
                route="reject",
                fallback_used=False,
                attempts_total=0,
                models_tried=[],
                budget_exhausted=False,
                failure_reason="git_repo_unhealthy",
            )
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
        fast_edit_reason = str(fast_plan.get("reason") or "") if fast_plan else ""
        if (
            not fast_plan
            and getattr(ctx, "is_direct_chat", False)
            and _env_bool(
                "OUROBOROS_CODEX_OFFLOAD_HEAVY_DIRECT_CHAT",
                default=_env_bool("OUROBOROS_OPENCODE_OFFLOAD_HEAVY_DIRECT_CHAT", default=False),
            )
        ):
            is_heavy, heavy_reason = _is_heavy_opencode_prompt(prompt)
            if is_heavy:
                return _finish(
                    _offload_patch_edit_to_worker(
                        ctx=ctx,
                        prompt=prompt,
                        cwd=cwd,
                        reason=heavy_reason,
                    ),
                    ok=True,
                    route="offload_to_worker",
                    fallback_used=False,
                    attempts_total=0,
                    models_tried=[],
                    budget_exhausted=False,
                    fast_edit_reason=heavy_reason,
                )
        if not fast_plan:
            return _finish(
                _patch_edit_format_message(),
                ok=False,
                route="invalid_prompt",
                fallback_used=False,
                attempts_total=0,
                models_tried=[],
                budget_exhausted=False,
                failure_reason="invalid_prompt",
            )

        ctx.emit_progress_fn("Applying local patch edit...")
        fast = _apply_fast_edit(ctx, work_dir=work_dir, plan=fast_plan)
        if not fast.get("ok"):
            return _finish(
                _patch_edit_format_message(str(fast.get("error") or "patch apply failed")),
                ok=False,
                route="patch_failed",
                fallback_used=False,
                attempts_total=0,
                models_tried=[],
                budget_exhausted=False,
                failure_reason="patch_failed",
                fast_edit_reason=fast_edit_reason,
            )

        out = (
            "✅ PATCH_EDIT_APPLIED: "
            f"{fast.get('file')} "
            f"(replacements={int(fast.get('replacements_applied') or 0)}, method={fast.get('method')})"
        )
        if repo_note:
            out = f"{repo_note}\n{out}"
        warning = _check_uncommitted_changes(ctx.repo_dir, source="patch_edit")
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
    except Exception as e:
        return _finish(
            f"⚠️ PATCH_EDIT_FAILED: {type(e).__name__}: {e}",
            ok=False,
            route="patch_edit",
            fallback_used=False,
            attempts_total=0,
            models_tried=[],
            budget_exhausted=False,
            failure_reason="unexpected_exception",
        )
    finally:
        _release_git_lock(lock)

def _opencode_edit(ctx: ToolContext, prompt: str, cwd: str = "") -> str:
    """Deprecated stub: external OpenCode path is disabled."""
    _append_tool_stats(
        ctx,
        {
            "ok": False,
            "route": "disabled",
            "fallback_used": False,
            "attempts_total": 0,
            "retries_used": 0,
            "models_tried": [],
            "budget_exhausted": False,
            "failure_reason": "tool_disabled",
            "fast_edit_reason": "",
            "fast_edit_method": "",
            "fast_edit_file": "",
            "fast_edit_replacements": 0,
            "duration_ms": 0,
        },
        tool_name="opencode_edit",
    )
    return (
        "⚠️ OPENCODE_EDIT_DISABLED: tool opencode_edit is disabled. "
        "Use patch_edit for code changes."
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
        }, _run_shell, is_code_tool=True, timeout_sec=_run_shell_timeout_sec()),
        ToolEntry("patch_edit", {
            "name": "patch_edit",
            "description": (
                "Edit code via local structured self-edit patches only. "
                "Use one atomic FILE/REPLACE/WITH/COUNT change per call. "
                "If git state is unhealthy, run git_repo_health(auto_recover=true) first. "
                "Follow with repo_commit_push."
            ),
            "parameters": {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string", "default": ""},
            }, "required": ["prompt"]},
        }, _patch_edit, is_code_tool=True, timeout_sec=300),
    ]
