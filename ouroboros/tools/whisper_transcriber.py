"""Whisper transcriber tool wrapper for external script-based ASR."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import safe_relpath


def _resolve_audio_path(ctx: ToolContext, audio_path: str) -> pathlib.Path:
    raw = str(audio_path or "").strip()
    if not raw:
        raise ValueError("audio_path must be a non-empty string")

    p = pathlib.Path(raw)
    if p.is_absolute():
        resolved = p.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"audio file not found: {resolved}")
        return resolved

    rel = safe_relpath(raw)
    repo_candidate = (ctx.repo_dir / rel).resolve()
    if repo_candidate.exists() and repo_candidate.is_file():
        return repo_candidate

    drive_candidate = (ctx.drive_root / rel).resolve()
    if drive_candidate.exists() and drive_candidate.is_file():
        return drive_candidate

    raise FileNotFoundError(
        f"audio file not found in repo/drive: {raw} "
        f"(checked: {repo_candidate}, {drive_candidate})"
    )


def _whisper_transcriber(
    ctx: ToolContext,
    audio_path: str,
    args: List[str] | None = None,
    cwd: str = "",
) -> str:
    """Run external whisper transcriber script and return stdout/stderr."""
    script_path = pathlib.Path(
        os.environ.get("OUROBOROS_WHISPER_SCRIPT", "/app/tools/whisper_transcriber.py")
    ).resolve()
    if not script_path.exists():
        return (
            "⚠️ WHISPER_SCRIPT_NOT_FOUND: "
            f"{script_path}. Set OUROBOROS_WHISPER_SCRIPT to a valid script path."
        )

    try:
        audio_file = _resolve_audio_path(ctx, audio_path)
    except Exception as e:
        return f"⚠️ WHISPER_INPUT_ERROR: {e}"

    work_dir = ctx.repo_dir
    if cwd and cwd.strip() not in ("", ".", "./"):
        candidate = (ctx.repo_dir / cwd).resolve()
        if candidate.exists() and candidate.is_dir():
            work_dir = candidate

    cmd = [
        shutil.which("python3") or "python3",
        str(script_path),
        str(audio_file),
    ]
    if args:
        cmd.extend(str(a) for a in args)

    try:
        res = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return "⚠️ WHISPER_TIMEOUT: command exceeded 300s."
    except Exception as e:
        return f"⚠️ WHISPER_ERROR: {e}"

    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    payload = f"exit_code={res.returncode}\n{out}"
    if err:
        payload += f"\n--- STDERR ---\n{err}"
    return payload.strip()


def get_tools():
    return [
        ToolEntry(
            "whisper_transcriber",
            {
                "name": "whisper_transcriber",
                "description": (
                    "Transcribe audio using external Whisper script "
                    "(default: /app/tools/whisper_transcriber.py). "
                    "Pass extra CLI args via 'args'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "audio_path": {"type": "string"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": [],
                        },
                        "cwd": {"type": "string", "default": ""},
                    },
                    "required": ["audio_path"],
                },
            },
            _whisper_transcriber,
        ),
    ]

