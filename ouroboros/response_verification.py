"""
Runtime fact-check gate for agent user-facing responses.

Purpose:
- detect high-risk concrete claims in free-form text
- verify them against local machine evidence (git/files/tool trace)
- block unverified claims from being presented as facts
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from ouroboros.utils import safe_resolve_under_root


_RE_COMMIT = re.compile(r"\b(?:commit|коммит)\s*[:#]?\s*([0-9a-f]{7,40})\b", re.IGNORECASE)
_RE_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9_+-]+")

_FILE_ASSERTIVE_MARKERS = (
    "создан",
    "создала",
    "создали",
    "добавлен",
    "добавила",
    "добавили",
    "написан",
    "написала",
    "written",
    "created",
    "added",
    "generated",
    "module",
    "file",
    "модуль",
    "файл",
)

_TESTS_PASS_MARKERS = (
    "все тесты прошли",
    "тесты прошли",
    "tests passed",
    "all tests passed",
    "pytest passed",
    "green tests",
)

_TEST_SUCCESS_MARKERS = (
    "passed",
    "0 failed",
    "0 errors",
    "успешно",
    "ok",
)
_TEST_FAILURE_MARKERS = (
    "traceback",
    "timeout",
    "timed out",
    "⚠️",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int, min_value: int = 1, max_value: int = 100) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip()) if raw is not None else int(default)
    except Exception:
        value = int(default)
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


@dataclass(frozen=True)
class FactFinding:
    kind: str
    claim: str
    ok: bool
    reason: str


def _repo_has_git(repo_dir: pathlib.Path) -> bool:
    return (repo_dir / ".git").exists()


def _verify_commit_exists(repo_dir: pathlib.Path, sha: str) -> FactFinding:
    claim = f"commit {sha}"
    if not _repo_has_git(repo_dir):
        return FactFinding(kind="commit", claim=claim, ok=False, reason="git repo not initialized")
    try:
        res = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception as exc:
        return FactFinding(kind="commit", claim=claim, ok=False, reason=f"git check error: {type(exc).__name__}")
    if res.returncode == 0:
        return FactFinding(kind="commit", claim=claim, ok=True, reason="verified")
    return FactFinding(kind="commit", claim=claim, ok=False, reason="commit not found")


def _is_assertive_file_claim(text: str, match: re.Match[str]) -> bool:
    lo = max(0, match.start() - 72)
    hi = min(len(text), match.end() + 32)
    window = text[lo:hi].lower()
    return any(marker in window for marker in _FILE_ASSERTIVE_MARKERS)


def _verify_file_exists(repo_dir: pathlib.Path, rel_path: str) -> FactFinding:
    claim = rel_path
    try:
        path = safe_resolve_under_root(repo_dir, rel_path)
    except Exception:
        return FactFinding(kind="file", claim=claim, ok=False, reason="unsafe path")
    if path.exists():
        return FactFinding(kind="file", claim=claim, ok=True, reason="exists")
    return FactFinding(kind="file", claim=claim, ok=False, reason="file not found")


def _extract_test_claims(text: str) -> List[str]:
    low = str(text or "").lower()
    if "не все тесты прошли" in low or "tests did not pass" in low:
        return []
    if any(marker in low for marker in _TESTS_PASS_MARKERS):
        return ["tests_passed"]
    return []


def _tool_results_from_trace(llm_trace: Optional[Dict[str, Any]]) -> Sequence[Dict[str, Any]]:
    if not isinstance(llm_trace, dict):
        return []
    calls = llm_trace.get("tool_calls")
    if not isinstance(calls, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in calls:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        if tool not in {"run_shell", "opencode_edit", "repo_commit_push"}:
            continue
        out.append(item)
    return out


def _looks_like_test_output(text: str) -> bool:
    markers = (
        "pytest",
        " passed",
        " failed",
        " error",
        " errors",
        "ran ",
        "tests in ",
        "collected ",
        "test session starts",
    )
    return any(marker in text for marker in markers)


def _has_test_failure_marker(text: str) -> bool:
    if any(marker in text for marker in _TEST_FAILURE_MARKERS):
        return True
    if re.search(r"\b[1-9]\d*\s+failed\b", text):
        return True
    if re.search(r"\b[1-9]\d*\s+errors?\b", text):
        return True
    return False


def _verify_tests_passed_claim(llm_trace: Optional[Dict[str, Any]]) -> FactFinding:
    claim = "тесты прошли"
    tool_results = _tool_results_from_trace(llm_trace)
    if not tool_results:
        return FactFinding(kind="tests", claim=claim, ok=False, reason="нет подтверждения в выводе инструментов")

    for item in tool_results:
        raw = str(item.get("result") or "")
        low = raw.lower()
        if not _looks_like_test_output(low):
            continue
        if any(marker in low for marker in _TEST_SUCCESS_MARKERS) and not _has_test_failure_marker(low):
            return FactFinding(kind="tests", claim=claim, ok=True, reason="инструменты показали успешный прогон")

    return FactFinding(kind="tests", claim=claim, ok=False, reason="в трассе нет успешного вывода тестов")


def collect_fact_findings(
    *,
    text: str,
    repo_dir: pathlib.Path,
    llm_trace: Optional[Dict[str, Any]] = None,
) -> List[FactFinding]:
    findings: List[FactFinding] = []
    content = str(text or "")
    if not content.strip():
        return findings

    # Commit claims (commit <sha>, коммит <sha>)
    seen_commits: set[str] = set()
    for sha in _RE_COMMIT.findall(content):
        sha = str(sha).strip()
        if not sha or sha in seen_commits:
            continue
        seen_commits.add(sha)
        findings.append(_verify_commit_exists(repo_dir, sha))

    # File claims with assertive context (created/added/etc near path)
    seen_paths: set[str] = set()
    for match in _RE_PATH.finditer(content):
        rel_path = str(match.group(0)).strip()
        if not rel_path or rel_path in seen_paths:
            continue
        if not _is_assertive_file_claim(content, match):
            continue
        seen_paths.add(rel_path)
        findings.append(_verify_file_exists(repo_dir, rel_path))

    # Tests-passed claims
    for claim in _extract_test_claims(content):
        if claim == "tests_passed":
            findings.append(_verify_tests_passed_claim(llm_trace))

    return findings


def apply_fact_verification_gate(
    *,
    text: str,
    repo_dir: pathlib.Path,
    llm_trace: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Verify concrete factual claims. If any claim is unverified, return a guarded message.
    """
    if not _env_bool("OUROBOROS_FACT_GATE_ENABLED", default=True):
        return str(text or "")

    findings = collect_fact_findings(text=text, repo_dir=repo_dir, llm_trace=llm_trace)
    unverified = [f for f in findings if not f.ok]
    if not unverified:
        return str(text or "")

    max_items = _env_int("OUROBOROS_FACT_GATE_MAX_FINDINGS", 6, min_value=1, max_value=30)
    lines = [
        "⚠️ Автопроверка фактов: часть заявлений не подтверждена локально.",
        "Ниже список неподтверждённых пунктов:",
    ]
    for finding in unverified[:max_items]:
        lines.append(f"- `{finding.claim}`: не подтверждено ({finding.reason})")

    lines.append(
        "Считаю эти пункты неподтверждёнными до явной машинной проверки (git/files/tests)."
    )
    if _env_bool("OUROBOROS_FACT_GATE_APPEND_ORIGINAL", default=False):
        lines.append("")
        lines.append("Исходный самоотчёт агента:")
        lines.append(str(text or ""))

    return "\n".join(lines)
