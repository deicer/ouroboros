"""
Supervisor — Git operations.

Clone, checkout, reset, rescue snapshots, dependency sync, import test.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Tuple

from supervisor.state import (
    append_jsonl,
    atomic_write_text,
    load_state,
    save_state,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level config (set via init())
# ---------------------------------------------------------------------------
REPO_DIR: pathlib.Path = pathlib.Path(os.environ.get("OUROBOROS_REPO_DIR", "/app"))
DRIVE_ROOT: pathlib.Path = pathlib.Path(os.environ.get("DRIVE_ROOT", "/data"))
REMOTE_URL: str = ""
BRANCH_DEV: str = "ouroboros"
BRANCH_STABLE: str = "ouroboros-stable"


def init(repo_dir: pathlib.Path, drive_root: pathlib.Path, remote_url: str,
         branch_dev: str = "ouroboros", branch_stable: str = "ouroboros-stable") -> None:
    global REPO_DIR, DRIVE_ROOT, REMOTE_URL, BRANCH_DEV, BRANCH_STABLE
    REPO_DIR = repo_dir
    DRIVE_ROOT = drive_root
    REMOTE_URL = remote_url
    BRANCH_DEV = branch_dev
    BRANCH_STABLE = branch_stable


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_capture(cmd: List[str]) -> Tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=str(REPO_DIR), capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def ensure_repo_present() -> None:
    if not (REPO_DIR / ".git").exists():
        # Init git in-place (don't rm -rf /app — we're running from it!)
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(REPO_DIR), check=True)
        subprocess.run(["git", "remote", "add", "origin", REMOTE_URL],
                        cwd=str(REPO_DIR), check=True)
    else:
        subprocess.run(["git", "remote", "set-url", "origin", REMOTE_URL],
                        cwd=str(REPO_DIR), check=True)
    subprocess.run(["git", "config", "user.name", "Ouroboros"], cwd=str(REPO_DIR), check=True)
    subprocess.run(["git", "config", "user.email", "ouroboros@users.noreply.github.com"],
                    cwd=str(REPO_DIR), check=True)
    subprocess.run(["git", "fetch", "origin"], cwd=str(REPO_DIR), check=True)


# ---------------------------------------------------------------------------
# Repo sync state collection
# ---------------------------------------------------------------------------

def _collect_repo_sync_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "current_branch": "unknown",
        "dirty_lines": [],
        "unpushed_lines": [],
        "warnings": [],
    }

    rc, branch, err = git_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0 and branch:
        state["current_branch"] = branch
    elif err:
        state["warnings"].append(f"branch_error:{err}")

    rc, dirty, err = git_capture(["git", "status", "--porcelain"])
    if rc == 0 and dirty:
        state["dirty_lines"] = [ln for ln in dirty.splitlines() if ln.strip()]
    elif rc != 0 and err:
        state["warnings"].append(f"status_error:{err}")

    upstream = ""
    rc, up, err = git_capture(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if rc == 0 and up:
        upstream = up
    else:
        current_branch = str(state.get("current_branch") or "")
        if current_branch not in ("", "HEAD", "unknown"):
            upstream = f"origin/{current_branch}"
        elif err:
            state["warnings"].append(f"upstream_error:{err}")

    if upstream:
        rc, unpushed, err = git_capture(["git", "log", "--oneline", f"{upstream}..HEAD"])
        if rc == 0 and unpushed:
            state["unpushed_lines"] = [ln for ln in unpushed.splitlines() if ln.strip()]
        elif rc != 0 and err:
            state["warnings"].append(f"unpushed_error:{err}")

    return state


def _copy_untracked_for_rescue(dst_root: pathlib.Path, max_files: int = 200,
                                max_total_bytes: int = 12_000_000) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "copied_files": 0, "skipped_files": 0, "copied_bytes": 0, "truncated": False,
    }
    rc, txt, err = git_capture(["git", "ls-files", "--others", "--exclude-standard"])
    if rc != 0:
        out["error"] = err or "git ls-files failed"
        return out

    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return out

    dst_root.mkdir(parents=True, exist_ok=True)
    for rel in lines:
        if out["copied_files"] >= max_files:
            out["truncated"] = True
            break
        src = (REPO_DIR / rel).resolve()
        try:
            src.relative_to(REPO_DIR.resolve())
        except Exception:
            out["skipped_files"] += 1
            continue
        if not src.exists() or not src.is_file():
            out["skipped_files"] += 1
            continue
        try:
            size = int(src.stat().st_size)
        except Exception:
            out["skipped_files"] += 1
            continue
        if (out["copied_bytes"] + size) > max_total_bytes:
            out["truncated"] = True
            break
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            out["copied_files"] += 1
            out["copied_bytes"] += size
        except Exception:
            out["skipped_files"] += 1
    return out


def _create_rescue_snapshot(branch: str, reason: str,
                             repo_state: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rescue_dir = DRIVE_ROOT / "archive" / "rescue" / f"{ts}_{uuid.uuid4().hex[:8]}"
    rescue_dir.mkdir(parents=True, exist_ok=True)

    info: Dict[str, Any] = {
        "ts": now.isoformat(),
        "target_branch": branch,
        "reason": reason,
        "current_branch": repo_state.get("current_branch"),
        "dirty_count": len(repo_state.get("dirty_lines") or []),
        "unpushed_count": len(repo_state.get("unpushed_lines") or []),
        "warnings": list(repo_state.get("warnings") or []),
        "path": str(rescue_dir),
    }

    rc_status, status_txt, _ = git_capture(["git", "status", "--porcelain"])
    if rc_status == 0:
        atomic_write_text(rescue_dir / "status.porcelain.txt",
                          status_txt + ("\n" if status_txt else ""))

    rc_diff, diff_txt, diff_err = git_capture(["git", "diff", "--binary", "HEAD"])
    if rc_diff == 0:
        atomic_write_text(rescue_dir / "changes.diff",
                          diff_txt + ("\n" if diff_txt else ""))
    else:
        info["diff_error"] = diff_err or "git diff failed"

    untracked_meta = _copy_untracked_for_rescue(rescue_dir / "untracked")
    info["untracked"] = untracked_meta

    unpushed_lines = [ln for ln in (repo_state.get("unpushed_lines") or []) if str(ln).strip()]
    if unpushed_lines:
        atomic_write_text(rescue_dir / "unpushed_commits.txt",
                          "\n".join(unpushed_lines) + "\n")

    atomic_write_text(rescue_dir / "rescue_meta.json",
                      json.dumps(info, ensure_ascii=False, indent=2))
    return info


def _is_valid_branch_name(branch: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_./-]+$", str(branch or "").strip()))


def _attempt_auto_preserve_unsynced(reason: str, repo_state: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort auto-preserve for unsynced state before destructive reset.

    Strategy:
    - If there are local modifications/untracked: stage + commit.
    - Always try to push current branch (also flushes pre-existing unpushed commits).
    - If commit was created but push failed, undo that commit to keep tree unchanged.

    Controlled by env OUROBOROS_AUTO_PRESERVE_UNSYNCED (default: 1).
    """
    enabled = str(os.environ.get("OUROBOROS_AUTO_PRESERVE_UNSYNCED", "1")).strip().lower() not in {
        "0", "false", "off", "no",
    }
    info: Dict[str, Any] = {
        "enabled": enabled,
        "attempted": False,
        "ok": False,
        "committed": False,
        "pushed": False,
        "branch": str(repo_state.get("current_branch") or ""),
    }
    if not enabled:
        return info

    dirty_lines = list(repo_state.get("dirty_lines") or [])
    unpushed_lines = list(repo_state.get("unpushed_lines") or [])
    if not dirty_lines and not unpushed_lines:
        info["ok"] = True
        return info

    branch = str(repo_state.get("current_branch") or "").strip()
    if not branch or branch in {"HEAD", "unknown"} or not _is_valid_branch_name(branch):
        info["error"] = f"invalid_current_branch:{branch or 'empty'}"
        return info

    info["attempted"] = True
    commit_created = False
    try:
        subprocess.run(["git", "checkout", branch, "--"], cwd=str(REPO_DIR), check=True)

        # Commit only when there are actual staged changes.
        if dirty_lines:
            subprocess.run(["git", "add", "-A"], cwd=str(REPO_DIR), check=True)
            diff_cached = subprocess.run(
                ["git", "diff", "--cached", "--quiet"], cwd=str(REPO_DIR), check=False
            )
            if diff_cached.returncode == 1:
                commit_msg = f"auto-preserve: unsynced changes before reset ({reason})"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(REPO_DIR), check=True)
                commit_created = True
                info["committed"] = True
            elif diff_cached.returncode not in (0,):
                raise RuntimeError(f"git diff --cached failed with rc={diff_cached.returncode}")

        # Best effort rebase; push is strict.
        subprocess.run(["git", "pull", "--rebase", "origin", branch], cwd=str(REPO_DIR), check=False)
        subprocess.run(["git", "push", "origin", branch], cwd=str(REPO_DIR), check=True)
        info["pushed"] = True
        info["ok"] = True
        return info
    except Exception as e:
        if commit_created:
            try:
                # Keep changes in working tree while dropping temporary local commit.
                subprocess.run(["git", "reset", "HEAD~1"], cwd=str(REPO_DIR), check=False)
            except Exception:
                pass
        info["error"] = repr(e)
        return info


# ---------------------------------------------------------------------------
# Checkout + reset
# ---------------------------------------------------------------------------

def checkout_and_reset(branch: str, reason: str = "unspecified",
                       unsynced_policy: str = "ignore") -> Tuple[bool, str]:
    rc, _, err = git_capture(["git", "fetch", "origin"])
    if rc != 0:
        msg = f"git fetch failed: {err or 'unknown error'}"
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "reset_fetch_failed",
                "target_branch": branch, "reason": reason, "error": msg,
            },
        )
        return False, msg

    policy = str(unsynced_policy or "ignore").strip().lower()
    if policy not in {"ignore", "block", "rescue_and_block", "rescue_and_reset"}:
        policy = "ignore"

    if policy != "ignore":
        repo_state = _collect_repo_sync_state()
        dirty_lines = list(repo_state.get("dirty_lines") or [])
        unpushed_lines = list(repo_state.get("unpushed_lines") or [])
        if dirty_lines or unpushed_lines:
            # First try auto-preserve (commit/push) to avoid losing local edits on restart.
            auto_preserve: Dict[str, Any] = {}
            if policy in {"rescue_and_block", "rescue_and_reset"}:
                try:
                    auto_preserve = _attempt_auto_preserve_unsynced(reason=reason, repo_state=repo_state)
                except Exception as e:
                    auto_preserve = {"attempted": True, "ok": False, "error": repr(e)}

                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "type": "auto_preserve_unsynced_attempt",
                        "target_branch": branch,
                        "reason": reason,
                        "policy": policy,
                        "before_dirty_count": len(dirty_lines),
                        "before_unpushed_count": len(unpushed_lines),
                        "result": auto_preserve,
                    },
                )

                if auto_preserve.get("ok"):
                    # Re-evaluate sync state after successful preserve.
                    repo_state = _collect_repo_sync_state()
                    dirty_lines = list(repo_state.get("dirty_lines") or [])
                    unpushed_lines = list(repo_state.get("unpushed_lines") or [])

            # Auto-preserve fully resolved unsynced state: continue with normal checkout flow.
            if not (dirty_lines or unpushed_lines):
                pass
            else:
                rescue_info: Dict[str, Any] = {}
                if policy in {"rescue_and_block", "rescue_and_reset"}:
                    try:
                        rescue_info = _create_rescue_snapshot(
                            branch=branch, reason=reason, repo_state=repo_state)
                    except Exception as e:
                        rescue_info = {"error": repr(e)}
                bits: List[str] = []
                if unpushed_lines:
                    bits.append(f"unpushed={len(unpushed_lines)}")
                if dirty_lines:
                    bits.append(f"dirty={len(dirty_lines)}")
                detail = ", ".join(bits) if bits else "unsynced"
                rescue_suffix = ""
                rescue_path = str(rescue_info.get("path") or "").strip()
                if rescue_path:
                    rescue_suffix = f" Rescue saved to {rescue_path}."
                elif policy in {"rescue_and_block", "rescue_and_reset"} and rescue_info.get("error"):
                    rescue_suffix = f" Rescue failed: {rescue_info.get('error')}."

                if policy in {"block", "rescue_and_block"}:
                    msg = f"Reset blocked ({detail}) to protect local changes.{rescue_suffix}"
                    append_jsonl(
                        DRIVE_ROOT / "logs" / "supervisor.jsonl",
                        {
                            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "type": "reset_blocked_unsynced_state",
                            "target_branch": branch, "reason": reason, "policy": policy,
                            "current_branch": repo_state.get("current_branch"),
                            "dirty_count": len(dirty_lines),
                            "unpushed_count": len(unpushed_lines),
                            "dirty_preview": dirty_lines[:20],
                            "unpushed_preview": unpushed_lines[:20],
                            "warnings": list(repo_state.get("warnings") or []),
                            "rescue": rescue_info,
                        },
                    )
                    return False, msg

                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "type": "reset_unsynced_rescued_then_reset",
                        "target_branch": branch, "reason": reason, "policy": policy,
                        "current_branch": repo_state.get("current_branch"),
                        "dirty_count": len(dirty_lines),
                        "unpushed_count": len(unpushed_lines),
                        "dirty_preview": dirty_lines[:20],
                        "unpushed_preview": unpushed_lines[:20],
                        "warnings": list(repo_state.get("warnings") or []),
                        "rescue": rescue_info,
                    },
                )
                # Policy allows destructive reset after rescue snapshot.
                # Clean working tree up-front to avoid checkout failures caused by
                # local modifications or untracked files "in the way".
                if policy == "rescue_and_reset":
                    subprocess.run(["git", "reset", "--hard"], cwd=str(REPO_DIR), check=False)
                    subprocess.run(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=False)

    rc_verify = subprocess.run(
        ["git", "rev-parse", "--verify", f"origin/{branch}"],
        cwd=str(REPO_DIR), capture_output=True,
    ).returncode
    if rc_verify != 0:
        # Branch doesn't exist on remote — create it from main (first-time setup)
        rc_main = subprocess.run(
            ["git", "rev-parse", "--verify", "origin/main"],
            cwd=str(REPO_DIR), capture_output=True,
        ).returncode
        if rc_main != 0:
            msg = f"Branch {branch} not found on remote and origin/main missing"
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "type": "reset_branch_missing",
                    "target_branch": branch, "reason": reason,
                },
            )
            return False, msg
        log.info("Branch %s not on remote — creating from origin/main", branch)
        checkout_create_cmd = ["git", "checkout", "-b", branch, "origin/main", "--"]
        if policy == "rescue_and_reset":
            checkout_create_cmd = ["git", "checkout", "-f", "-b", branch, "origin/main", "--"]
        subprocess.run(checkout_create_cmd,
                        cwd=str(REPO_DIR), check=True)
        subprocess.run(["git", "push", "-u", "origin", branch],
                        cwd=str(REPO_DIR), check=True)
    else:
        checkout_cmd = ["git", "checkout", branch, "--"]
        if policy == "rescue_and_reset":
            checkout_cmd = ["git", "checkout", "-f", branch, "--"]
        subprocess.run(checkout_cmd, cwd=str(REPO_DIR), check=True)
        subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=str(REPO_DIR), check=True)
    # Clean __pycache__ to prevent stale bytecode (git checkout may not update mtime)
    for p in REPO_DIR.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    st = load_state()
    st["current_branch"] = branch
    st["current_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO_DIR),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    save_state(st)
    return True, "ok"


# ---------------------------------------------------------------------------
# Dependencies + import test
# ---------------------------------------------------------------------------

def sync_runtime_dependencies(reason: str) -> Tuple[bool, str]:
    req_path = REPO_DIR / "requirements.txt"
    cmd: List[str] = [sys.executable, "-m", "pip", "install", "-q"]
    source = ""
    if req_path.exists():
        cmd += ["-r", str(req_path)]
        source = f"requirements:{req_path}"
    else:
        cmd += ["openai>=1.0.0", "requests"]
        source = "fallback:minimal"
    try:
        subprocess.run(cmd, cwd=str(REPO_DIR), check=True)
    except Exception as e:
        msg = repr(e)
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "deps_sync_error", "reason": reason, "source": source, "error": msg,
            },
        )
        return False, msg

    extras_raw = os.environ.get("OUROBOROS_RUNTIME_EXTRA_PIP", "") or ""
    extras = [pkg.strip() for pkg in re.split(r"[,\n;]+", extras_raw) if pkg.strip()]
    strict_extras = _env_bool("OUROBOROS_RUNTIME_EXTRA_PIP_STRICT", default=False)
    extras_errors: List[Dict[str, str]] = []
    extras_installed: List[str] = []

    for pkg in extras:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", pkg],
                cwd=str(REPO_DIR),
                check=True,
            )
            extras_installed.append(pkg)
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "type": "deps_sync_extra_ok",
                    "reason": reason,
                    "package": pkg,
                },
            )
        except Exception as e:
            err = repr(e)
            extras_errors.append({"package": pkg, "error": err})
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "type": "deps_sync_extra_error",
                    "reason": reason,
                    "package": pkg,
                    "error": err,
                    "strict": strict_extras,
                },
            )

    append_jsonl(
        DRIVE_ROOT / "logs" / "supervisor.jsonl",
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": "deps_sync_ok",
            "reason": reason,
            "source": source,
            "extras_requested": extras,
            "extras_installed": extras_installed,
            "extras_errors": extras_errors,
            "extras_strict": strict_extras,
        },
    )
    if extras_errors and strict_extras:
        return False, f"Optional deps failed (strict): {extras_errors}"
    return True, source


def import_test() -> Dict[str, Any]:
    r = subprocess.run(
        ["python3", "-c", "import ouroboros, ouroboros.agent; print('import_ok')"],
        cwd=str(REPO_DIR),
        capture_output=True, text=True,
    )
    return {"ok": (r.returncode == 0), "stdout": r.stdout, "stderr": r.stderr,
            "returncode": r.returncode}


def _trim_log_text(text: str, max_len: int = 4000) -> str:
    s = str(text or "")
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n...(truncated, total={len(s)} chars)"


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


def _run_runtime_validation(branch_name: str, reason: str) -> Dict[str, Any]:
    """Run post-self-modification runtime checks (syntax, lint criticals, tests)."""
    strict_ruff = _env_bool("OUROBOROS_RUNTIME_VALIDATE_RUFF_STRICT", default=False)
    strict_pytest = _env_bool("OUROBOROS_RUNTIME_VALIDATE_PYTEST_STRICT", default=False)

    checks = [
        (
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "launcher.py", "ouroboros", "supervisor", "tests"],
            180,
        ),
        (
            "ruff_ef",
            [sys.executable, "-m", "ruff", "check", "--select=E,F", "launcher.py", "ouroboros", "supervisor", "tests"],
            180,
        ),
        (
            "pytest",
            [sys.executable, "-m", "pytest", "tests/", "-x", "-q"],
            600,
        ),
    ]

    steps: List[Dict[str, Any]] = []
    failed_step = ""
    ok = True
    non_blocking_failures: List[str] = []

    for name, cmd, timeout_sec in checks:
        try:
            r = subprocess.run(
                cmd,
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            step = {
                "name": name,
                "cmd": cmd,
                "returncode": int(r.returncode),
                "stdout": _trim_log_text(r.stdout or ""),
                "stderr": _trim_log_text(r.stderr or ""),
                "timeout_sec": timeout_sec,
                "ok": bool(r.returncode == 0),
            }
            steps.append(step)
            if r.returncode != 0:
                is_non_blocking = (
                    (name == "ruff_ef" and not strict_ruff)
                    or (name == "pytest" and not strict_pytest)
                )
                if is_non_blocking:
                    step["non_blocking"] = True
                    non_blocking_failures.append(name)
                    continue
                ok = False
                failed_step = name
                break
        except subprocess.TimeoutExpired as e:
            step = {
                "name": name,
                "cmd": cmd,
                "returncode": -1,
                "stdout": _trim_log_text(getattr(e, "stdout", "") or ""),
                "stderr": _trim_log_text(getattr(e, "stderr", "") or ""),
                "timeout_sec": timeout_sec,
                "ok": False,
                "error": f"timeout_after_{timeout_sec}s",
            }
            is_non_blocking = (
                (name == "ruff_ef" and not strict_ruff)
                or (name == "pytest" and not strict_pytest)
            )
            if is_non_blocking:
                step["non_blocking"] = True
                non_blocking_failures.append(name)
                steps.append(step)
                continue
            ok = False
            failed_step = name
            steps.append(step)
            break
        except Exception as e:
            step = {
                "name": name,
                "cmd": cmd,
                "returncode": -1,
                "stdout": "",
                "stderr": _trim_log_text(repr(e)),
                "timeout_sec": timeout_sec,
                "ok": False,
                "error": "execution_error",
            }
            is_non_blocking = (
                (name == "ruff_ef" and not strict_ruff)
                or (name == "pytest" and not strict_pytest)
            )
            if is_non_blocking:
                step["non_blocking"] = True
                non_blocking_failures.append(name)
                steps.append(step)
                continue
            ok = False
            failed_step = name
            steps.append(step)
            break

    result = {
        "ok": ok,
        "failed_step": failed_step,
        "steps": steps,
        "non_blocking_failures": non_blocking_failures,
    }
    append_jsonl(
        DRIVE_ROOT / "logs" / "supervisor.jsonl",
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": "safe_restart_runtime_validation",
            "reason": reason,
            "branch": branch_name,
            "ok": ok,
            "failed_step": failed_step,
            "steps": steps,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Safe restart orchestration
# ---------------------------------------------------------------------------

def safe_restart(
    reason: str,
    unsynced_policy: str = "rescue_and_reset",
) -> Tuple[bool, str]:
    """
    Attempt to checkout dev branch, sync deps, verify imports, and run runtime validation.
    Falls back to stable branch if dev fails.

    Args:
        reason: Human-readable reason for the restart (logged to supervisor.jsonl)
        unsynced_policy: Policy for handling unsynced state (default: "rescue_and_reset")

    Returns:
        Tuple of (ok: bool, message: str)
        - If successful: (True, "OK: <branch>")
        - If failed: (False, "<error description>")
    """
    # Try dev branch
    ok, err = checkout_and_reset(BRANCH_DEV, reason=reason, unsynced_policy=unsynced_policy)
    if not ok:
        return False, f"Failed checkout {BRANCH_DEV}: {err}"

    deps_ok, deps_msg = sync_runtime_dependencies(reason=reason)
    if not deps_ok:
        return False, f"Failed deps for {BRANCH_DEV}: {deps_msg}"

    t = import_test()
    if t["ok"]:
        validation = _run_runtime_validation(branch_name=BRANCH_DEV, reason=reason)
        if validation.get("ok"):
            return True, f"OK: {BRANCH_DEV}"
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "safe_restart_dev_validation_failed",
                "reason": reason,
                "branch": BRANCH_DEV,
                "failed_step": validation.get("failed_step") or "",
                "steps": validation.get("steps") or [],
            },
        )

    # Dev branch failed import — log the failure and fall back to latest stable tag
    if not t["ok"]:
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "safe_restart_dev_import_failed",
                "reason": reason,
                "branch": BRANCH_DEV,
                "stdout": t.get("stdout", ""),
                "stderr": t.get("stderr", ""),
                "returncode": t.get("returncode", -1),
            },
        )

    # Fallback: find latest stable tag and reset to it
    rc_tag, tag_out, tag_err = git_capture(
        ["git", "tag", "--list", "stable-*", "--sort=-creatordate"]
    )
    latest_tag = ""
    if rc_tag == 0 and tag_out.strip():
        latest_tag = tag_out.strip().splitlines()[0].strip()

    if latest_tag:
        log.info("Falling back to stable tag: %s", latest_tag)
        subprocess.run(
            ["git", "reset", "--hard", latest_tag],
            cwd=str(REPO_DIR), check=True,
        )
        deps_ok_s, deps_msg_s = sync_runtime_dependencies(reason=f"{reason}_fallback_tag")
        if not deps_ok_s:
            return False, f"Failed deps after fallback to {latest_tag}: {deps_msg_s}"
        t2 = import_test()
        if t2["ok"]:
            validation2 = _run_runtime_validation(branch_name=latest_tag, reason=f"{reason}_fallback_tag")
            if validation2.get("ok"):
                return True, f"OK: fell back to tag {latest_tag}"
            return False, f"Fallback tag {latest_tag} failed validation: {validation2.get('failed_step') or 'unknown'}"
        return False, f"Tag {latest_tag} also failed import"

    # No stable tag found — try legacy ouroboros-stable branch
    ok_s, err_s = checkout_and_reset(
        BRANCH_STABLE,
        reason=f"{reason}_fallback_stable",
        unsynced_policy="rescue_and_reset",
    )
    if not ok_s:
        return False, f"No stable tags found, failed checkout {BRANCH_STABLE}: {err_s}"

    deps_ok_s, deps_msg_s = sync_runtime_dependencies(reason=f"{reason}_fallback_stable")
    if not deps_ok_s:
        return False, f"Failed deps for {BRANCH_STABLE}: {deps_msg_s}"

    t2 = import_test()
    if t2["ok"]:
        validation2 = _run_runtime_validation(branch_name=BRANCH_STABLE, reason=f"{reason}_fallback_stable")
        if validation2.get("ok"):
            return True, f"OK: fell back to {BRANCH_STABLE}"
        return False, f"{BRANCH_STABLE} failed validation: {validation2.get('failed_step') or 'unknown'}"

    return False, "All fallbacks failed import/validation (dev, stable tags, stable branch)"
