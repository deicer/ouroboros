"""Git tools: repo_commit_push, git_status, git_diff, git_repo_health."""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import utc_now_iso, safe_relpath, run_cmd

log = logging.getLogger(__name__)


def _capture_git(repo_dir: pathlib.Path, cmd: List[str]) -> Tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _resolve_git_dir(repo_dir: pathlib.Path) -> pathlib.Path:
    dot_git = repo_dir / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            raw = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return dot_git
        prefix = "gitdir:"
        if raw.lower().startswith(prefix):
            candidate = raw[len(prefix):].strip()
            return (repo_dir / candidate).resolve()
    return dot_git


def _inspect_git_repo_state(repo_dir: pathlib.Path) -> Dict[str, Any]:
    git_dir = _resolve_git_dir(repo_dir)
    rc_branch, branch, branch_err = _capture_git(repo_dir, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    rc_unmerged, unmerged_out, unmerged_err = _capture_git(
        repo_dir,
        ["git", "diff", "--name-only", "--diff-filter=U"],
    )
    index_lock = git_dir / "index.lock"
    try:
        index_lock_age_sec = max(0.0, time.time() - index_lock.stat().st_mtime) if index_lock.exists() else 0.0
    except Exception:
        index_lock_age_sec = 0.0
    unmerged_files = [line.strip() for line in unmerged_out.splitlines() if line.strip()] if rc_unmerged == 0 else []
    return {
        "git_dir": git_dir,
        "branch": branch if rc_branch == 0 else "",
        "branch_error": branch_err if rc_branch != 0 else "",
        "unmerged_files": unmerged_files,
        "unmerged_error": unmerged_err if rc_unmerged != 0 else "",
        "rebase_in_progress": (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists(),
        "merge_in_progress": (git_dir / "MERGE_HEAD").exists(),
        "cherry_pick_in_progress": (git_dir / "CHERRY_PICK_HEAD").exists(),
        "revert_in_progress": (git_dir / "REVERT_HEAD").exists(),
        "index_lock_exists": index_lock.exists(),
        "index_lock_age_sec": index_lock_age_sec,
        "detached_head": branch == "HEAD",
    }


def _format_git_repo_state(state: Dict[str, Any]) -> str:
    problems: List[str] = []
    if state.get("rebase_in_progress"):
        problems.append("rebase in progress")
    if state.get("merge_in_progress"):
        problems.append("merge in progress")
    if state.get("cherry_pick_in_progress"):
        problems.append("cherry-pick in progress")
    if state.get("revert_in_progress"):
        problems.append("revert in progress")
    if state.get("index_lock_exists"):
        age = int(float(state.get("index_lock_age_sec") or 0))
        problems.append(f"index.lock present ({age}s old)")
    unmerged_files = list(state.get("unmerged_files") or [])
    if unmerged_files:
        preview = ", ".join(unmerged_files[:5])
        if len(unmerged_files) > 5:
            preview += ", ..."
        problems.append(f"unmerged files: {preview}")
    branch = str(state.get("branch") or "").strip()
    if branch == "HEAD":
        problems.append("detached HEAD")
    if not problems:
        return "clean"
    return "; ".join(problems)


def _auto_recover_git_repo(ctx: ToolContext, state: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    actions: List[str] = []
    git_dir = pathlib.Path(state.get("git_dir") or _resolve_git_dir(ctx.repo_dir))
    lock_path = git_dir / "index.lock"
    stale_lock_sec = int(os.environ.get("OUROBOROS_GIT_INDEX_LOCK_STALE_SEC", "120") or "120")

    if state.get("index_lock_exists") and float(state.get("index_lock_age_sec") or 0) >= stale_lock_sec:
        try:
            lock_path.unlink()
            actions.append("removed stale index.lock")
        except FileNotFoundError:
            pass
        except Exception:
            log.debug("Failed to remove stale git index lock", exc_info=True)

    abort_steps = [
        ("rebase_in_progress", ["git", "rebase", "--abort"], "aborted rebase"),
        ("merge_in_progress", ["git", "merge", "--abort"], "aborted merge"),
        ("cherry_pick_in_progress", ["git", "cherry-pick", "--abort"], "aborted cherry-pick"),
        ("revert_in_progress", ["git", "revert", "--abort"], "aborted revert"),
    ]
    for key, cmd, label in abort_steps:
        if not state.get(key):
            continue
        try:
            run_cmd(cmd, cwd=ctx.repo_dir)
            actions.append(label)
        except Exception:
            log.debug("Failed git recovery step %s", label, exc_info=True)

    new_state = _inspect_git_repo_state(ctx.repo_dir)
    blocking = bool(
        new_state.get("rebase_in_progress")
        or new_state.get("merge_in_progress")
        or new_state.get("cherry_pick_in_progress")
        or new_state.get("revert_in_progress")
        or new_state.get("index_lock_exists")
        or list(new_state.get("unmerged_files") or [])
    )
    return (not blocking), actions, new_state


def _ensure_git_repo_ready(
    ctx: ToolContext,
    action: str = "git operation",
    auto_recover: bool = True,
) -> Tuple[bool, str]:
    state = _inspect_git_repo_state(ctx.repo_dir)
    blocking = bool(
        state.get("rebase_in_progress")
        or state.get("merge_in_progress")
        or state.get("cherry_pick_in_progress")
        or state.get("revert_in_progress")
        or state.get("index_lock_exists")
        or list(state.get("unmerged_files") or [])
    )
    if not blocking:
        return True, ""

    summary = _format_git_repo_state(state)
    if not auto_recover:
        return False, f"⚠️ GIT_REPO_UNHEALTHY: {summary}"

    ok, actions, recovered_state = _auto_recover_git_repo(ctx, state)
    recovered_summary = _format_git_repo_state(recovered_state)
    if ok:
        actions_text = ", ".join(actions) if actions else "no-op recovery"
        return True, f"⚠️ GIT_REPO_AUTO_RECOVERED: auto-recovered before {action}: {actions_text}"
    return False, (
        f"⚠️ GIT_REPO_UNHEALTHY: {summary}. "
        f"Auto-recovery before {action} was not enough; current state: {recovered_summary}"
    )


# --- Git lock ---

def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but not signalable by current user.
        return True
    except Exception:
        return False


def _read_lock_meta(lock_path: pathlib.Path) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return meta
    for line in raw.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        meta[k.strip()] = v.strip()
    return meta


def _acquire_git_lock(ctx: ToolContext, timeout_sec: int = 120) -> pathlib.Path:
    lock_dir = ctx.drive_path("locks")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "git.lock"
    stale_sec = 600
    legacy_stale_sec = int(os.environ.get("OUROBOROS_GIT_LOCK_LEGACY_STALE_SEC", "120") or "120")
    same_pid_stale_sec = int(os.environ.get("OUROBOROS_GIT_LOCK_SAME_PID_STALE_SEC", "180") or "180")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if lock_path.exists():
            try:
                age = time.time() - lock_path.stat().st_mtime
                meta = _read_lock_meta(lock_path)
                pid_raw = meta.get("pid", "")
                pid = int(pid_raw) if pid_raw.isdigit() else 0
                # If lock owner process is dead, treat lock as stale immediately.
                if pid and not _pid_is_alive(pid):
                    lock_path.unlink()
                    continue
                # If lock belongs to this process for too long, assume stuck worker thread.
                if pid and pid == os.getpid() and age > same_pid_stale_sec:
                    lock_path.unlink()
                    continue
                # Legacy lock format (no pid) uses a shorter stale timeout to recover
                # from pre-upgrade stale locks that cannot be validated via PID.
                if (not pid) and age > legacy_stale_sec:
                    lock_path.unlink()
                    continue
                # PID-aware locks fall back to conservative age-based stale cleanup.
                if age > stale_sec:
                    lock_path.unlink()
                    continue
            except (FileNotFoundError, OSError):
                pass
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                payload = (
                    f"locked_at={utc_now_iso()}\n"
                    f"pid={os.getpid()}\n"
                    f"task_id={ctx.task_id or ''}\n"
                )
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            return lock_path
        except FileExistsError:
            time.sleep(0.5)
    raise TimeoutError(f"Git lock not acquired within {timeout_sec}s: {lock_path}")


def _release_git_lock(lock_path: pathlib.Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# --- Pre-push test gate ---

MAX_TEST_OUTPUT = 12000


def _run_pre_push_tests(ctx: ToolContext) -> Optional[str]:
    """Run pre-push tests if enabled. Returns None if tests pass, error string if they fail."""
    # Guard against ctx=None
    if ctx is None:
        log.warning("_run_pre_push_tests called with ctx=None, skipping tests")
        return None

    if os.environ.get("OUROBOROS_PRE_PUSH_TESTS", "1") != "1":
        return None

    tests_dir = pathlib.Path(ctx.repo_dir) / "tests"
    if not tests_dir.exists():
        return None

    try:
        result = subprocess.run(
            ["pytest", "tests/", "-q", "--tb=line", "--no-header"],
            cwd=ctx.repo_dir,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return None

        # Truncate output if too long
        output = result.stdout + result.stderr
        if len(output) > MAX_TEST_OUTPUT:
            output = output[:MAX_TEST_OUTPUT] + "\n...(truncated)..."
        return output

    except subprocess.TimeoutExpired:
        return "⚠️ PRE_PUSH_TEST_ERROR: pytest timed out after 30 seconds"

    except FileNotFoundError:
        return "⚠️ PRE_PUSH_TEST_ERROR: pytest not installed or not found in PATH"

    except Exception as e:
        log.warning(f"Pre-push tests failed with exception: {e}", exc_info=True)
        return f"⚠️ PRE_PUSH_TEST_ERROR: Unexpected error running tests: {e}"


def _git_push_with_tests(ctx: ToolContext) -> tuple[bool, str]:
    """Run pre-push tests, then pull --rebase and push.

    Returns:
        (ok, note_or_error)
        - ok=True: push succeeded; note may contain non-blocking warnings.
        - ok=False: push failed or strict pre-push gate blocked the push.
    """
    note = ""
    test_error = _run_pre_push_tests(ctx)
    if test_error:
        strict = os.environ.get("OUROBOROS_PRE_PUSH_STRICT", "0") == "1"
        if strict:
            log.error("Pre-push tests failed, blocking push (strict mode)")
            ctx.last_push_succeeded = False
            return False, (
                "⚠️ PRE_PUSH_TESTS_FAILED: Tests failed, push blocked (strict mode).\n"
                f"{test_error}\nCommitted locally but NOT pushed. Fix tests and push manually."
            )
        log.warning("Pre-push tests failed, but continuing push (non-strict mode)")
        note = (
            "⚠️ PRE_PUSH_TESTS_FAILED_NON_BLOCKING: tests failed, but push allowed.\n"
            f"{test_error}"
        )
        try:
            if ctx is not None:
                ctx.pending_events.append({
                    "type": "pre_push_tests_failed_non_blocking",
                    "ts": utc_now_iso(),
                    "message": test_error[:2000],
                })
        except Exception:
            log.debug("Failed to append non-blocking pre-push warning event", exc_info=True)

    try:
        run_cmd(["git", "pull", "--rebase", "origin", ctx.branch_dev], cwd=ctx.repo_dir)
    except Exception as e:
        log.debug("Failed to pull --rebase before push", exc_info=True)
        ok, recover_note = _ensure_git_repo_ready(ctx, action="git pull --rebase", auto_recover=True)
        if not ok:
            return False, (
                f"⚠️ GIT_ERROR (pull --rebase): {e}\n"
                f"{recover_note}\nCommitted locally but NOT pushed."
            )
        if recover_note:
            note = f"{note}\n{recover_note}".strip()

    try:
        run_cmd(["git", "push", "origin", ctx.branch_dev], cwd=ctx.repo_dir)
    except Exception as e:
        return False, f"⚠️ GIT_ERROR (push): {e}\nCommitted locally but NOT pushed."

    return True, note


# --- Tool implementations ---

def _repo_commit_push(ctx: ToolContext, commit_message: str, paths: Optional[List[str]] = None) -> str:
    ctx.last_push_succeeded = False
    if not commit_message.strip():
        return "⚠️ ERROR: commit_message must be non-empty."
    lock = _acquire_git_lock(ctx)
    try:
        ok, repo_note = _ensure_git_repo_ready(ctx, action="repo_commit_push", auto_recover=True)
        if not ok:
            return repo_note
        try:
            run_cmd(["git", "checkout", ctx.branch_dev], cwd=ctx.repo_dir)
        except Exception as e:
            return f"⚠️ GIT_ERROR (checkout): {e}"
        if paths:
            try:
                safe_paths = [safe_relpath(p) for p in paths if str(p).strip()]
            except ValueError as e:
                return f"⚠️ PATH_ERROR: {e}"
            add_cmd = ["git", "add"] + safe_paths
        else:
            add_cmd = ["git", "add", "-A"]
        try:
            run_cmd(add_cmd, cwd=ctx.repo_dir)
        except Exception as e:
            return f"⚠️ GIT_ERROR (add): {e}"
        try:
            status = run_cmd(["git", "status", "--porcelain"], cwd=ctx.repo_dir)
        except Exception as e:
            return f"⚠️ GIT_ERROR (status): {e}"
        if not status.strip():
            return "⚠️ GIT_NO_CHANGES: nothing to commit."
        try:
            run_cmd(["git", "commit", "-m", commit_message], cwd=ctx.repo_dir)
        except Exception as e:
            return f"⚠️ GIT_ERROR (commit): {e}"

        push_ok, push_note = _git_push_with_tests(ctx)
        if not push_ok:
            return push_note
    finally:
        _release_git_lock(lock)
    ctx.last_push_succeeded = True
    result = f"OK: committed and pushed to {ctx.branch_dev}: {commit_message}"
    if repo_note:
        result = f"{repo_note}\n{result}"
    if push_note:
        result += f"\n{push_note}"
    if paths is not None:
        try:
            untracked = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=ctx.repo_dir)
            if untracked.strip():
                files = ", ".join(untracked.strip().split("\n"))
                result += f"\n⚠️ WARNING: untracked files remain: {files} — they are NOT in git. Use repo_commit_push without paths to add everything."
        except Exception:
            log.debug("Failed to check for untracked files after repo_commit_push", exc_info=True)
            pass
    return result


def _git_repo_health(ctx: ToolContext, auto_recover: bool = False) -> str:
    before = _inspect_git_repo_state(ctx.repo_dir)
    before_summary = _format_git_repo_state(before)
    lines = [
        f"repo: {ctx.repo_dir}",
        f"branch: {before.get('branch') or 'unknown'}",
        f"healthy: {'yes' if before_summary == 'clean' else 'no'}",
        f"state: {before_summary}",
    ]
    unmerged_files = list(before.get("unmerged_files") or [])
    if unmerged_files:
        lines.append("unmerged_files:")
        lines.extend(f"- {path}" for path in unmerged_files[:20])

    if not auto_recover:
        return "\n".join(lines)

    ok, note = _ensure_git_repo_ready(ctx, action="git_repo_health", auto_recover=True)
    after = _inspect_git_repo_state(ctx.repo_dir)
    after_summary = _format_git_repo_state(after)
    lines.append("")
    lines.append(f"recovery_ok: {'yes' if ok else 'no'}")
    if note:
        lines.append(note)
    lines.append(f"post_state: {after_summary}")
    return "\n".join(lines)


def _git_status(ctx: ToolContext) -> str:
    try:
        return run_cmd(["git", "status", "--porcelain"], cwd=ctx.repo_dir)
    except Exception as e:
        return f"⚠️ GIT_ERROR: {e}"


def _git_diff(ctx: ToolContext, staged: bool = False) -> str:
    try:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        return run_cmd(cmd, cwd=ctx.repo_dir)
    except Exception as e:
        return f"⚠️ GIT_ERROR: {e}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("repo_commit_push", {
            "name": "repo_commit_push",
            "description": "Commit + push already-changed files. Does pull --rebase before push.",
            "parameters": {"type": "object", "properties": {
                "commit_message": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Files to add (empty = git add -A)"},
            }, "required": ["commit_message"]},
        }, _repo_commit_push, is_code_tool=True),
        ToolEntry("git_status", {
            "name": "git_status",
            "description": "git status --porcelain",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _git_status, is_code_tool=True),
        ToolEntry("git_repo_health", {
            "name": "git_repo_health",
            "description": (
                "Inspect git repo health (rebase/merge/conflicts/index.lock/detached HEAD). "
                "Use auto_recover=true to safely abort stuck git operations before editing or committing."
            ),
            "parameters": {"type": "object", "properties": {
                "auto_recover": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, attempt safe git recovery (abort rebase/merge, remove stale index.lock).",
                },
            }, "required": []},
        }, _git_repo_health, is_code_tool=True),
        ToolEntry("git_diff", {
            "name": "git_diff",
            "description": "git diff (use staged=true to see staged changes after git add)",
            "parameters": {"type": "object", "properties": {
                "staged": {"type": "boolean", "default": False, "description": "If true, show staged changes (--staged)"},
            }, "required": []},
        }, _git_diff, is_code_tool=True),
    ]
