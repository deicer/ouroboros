"""Tool-call argument parsing helpers."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, Dict


def _strip_code_fences(text: str) -> str:
    s = str(text or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if not lines:
        return s
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_tool_call_arguments(raw: Any) -> Dict[str, Any]:
    """Parse tool arguments from LLM output.

    Tolerates:
    - markdown code fences
    - double-encoded JSON strings
    - valid JSON object followed by trailing commentary/junk
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("tool arguments must be a JSON object string")

    text = _strip_code_fences(raw)
    if not text:
        return {}

    decoder = json.JSONDecoder()
    parse_error: Exception | None = None

    for candidate in (text, text.lstrip()):
        try:
            parsed, end = decoder.raw_decode(candidate)
        except JSONDecodeError as exc:
            parse_error = exc
            continue

        while isinstance(parsed, str):
            nested = _strip_code_fences(parsed)
            try:
                parsed, _ = decoder.raw_decode(nested)
            except JSONDecodeError:
                break

        if isinstance(parsed, dict):
            return parsed

        raise ValueError("tool arguments must decode to a JSON object")

    # As a final fallback, try from the first object/array marker.
    first_brace = min((idx for idx in (text.find("{"), text.find("[")) if idx >= 0), default=-1)
    if first_brace > 0:
        sliced = text[first_brace:]
        parsed, _ = decoder.raw_decode(sliced)
        while isinstance(parsed, str):
            nested = _strip_code_fences(parsed)
            parsed, _ = decoder.raw_decode(nested)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("tool arguments must decode to a JSON object")

    if parse_error is not None:
        raise parse_error
    raise ValueError("tool arguments must decode to a JSON object")
