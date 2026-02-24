"""Web search tool."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry


def _tavily_search(query: str, api_key: str) -> Dict[str, Any]:
    """Search web via Tavily API (primary backend)."""
    base_url = (os.environ.get("OUROBOROS_TAVILY_BASE_URL", "https://api.tavily.com") or "").rstrip("/")
    url = f"{base_url}/search"
    max_results = int(os.environ.get("OUROBOROS_TAVILY_MAX_RESULTS", "5") or "5")
    max_results = max(1, min(max_results, 20))
    search_depth = (os.environ.get("OUROBOROS_TAVILY_SEARCH_DEPTH", "basic") or "basic").strip()
    topic = (os.environ.get("OUROBOROS_TAVILY_TOPIC", "general") or "general").strip()

    payload = {
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "max_results": max_results,
        "include_answer": "basic",
        "include_raw_content": False,
        "include_images": False,
        "include_favicon": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        raise RuntimeError(f"Tavily HTTP {e.code}: {body[:500]}")
    except Exception as e:
        raise RuntimeError(f"Tavily request failed: {e}")

    sources: List[Dict[str, Any]] = []
    for r in data.get("results", []) or []:
        sources.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score"),
        })

    return {
        "backend": "tavily",
        "query": data.get("query") or query,
        "answer": data.get("answer") or "(no answer)",
        "sources": sources,
        "response_time": data.get("response_time"),
        "request_id": data.get("request_id"),
        "usage": data.get("usage"),
    }


def _openai_web_search(query: str, api_key: str) -> Dict[str, Any]:
    """Fallback web search via OpenAI Responses web_search tool."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    model = (os.environ.get("OUROBOROS_WEBSEARCH_MODEL") or os.environ.get("OUROBOROS_MODEL") or "").strip()
    if not model:
        raise RuntimeError("OUROBOROS_WEBSEARCH_MODEL (or OUROBOROS_MODEL) is required for OpenAI web search")
    resp = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        tool_choice="auto",
        input=query,
    )
    d = resp.model_dump()
    text = ""
    for item in d.get("output", []) or []:
        if item.get("type") == "message":
            for block in item.get("content", []) or []:
                if block.get("type") in ("output_text", "text"):
                    text += block.get("text", "")
    return {
        "backend": "openai",
        "query": query,
        "answer": text or "(no answer)",
    }


def _web_search(ctx: ToolContext, query: str) -> str:
    tavily_key = (os.environ.get("TAVILY_API_KEY", "") or "").strip()
    openai_key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    errors: List[str] = []

    # Primary backend: Tavily
    if tavily_key:
        try:
            result = _tavily_search(query=query, api_key=tavily_key)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            errors.append(f"tavily: {e!r}")

    # Fallback backend: OpenAI web_search tool
    if openai_key:
        try:
            result = _openai_web_search(query=query, api_key=openai_key)
            if errors:
                result["warnings"] = errors
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            errors.append(f"openai: {e!r}")

    if errors:
        return json.dumps({
            "error": "web_search failed on all configured backends",
            "details": errors,
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "error": "No search backend configured. Set TAVILY_API_KEY (preferred) or OPENAI_API_KEY."
    }, ensure_ascii=False, indent=2)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("web_search", {
            "name": "web_search",
            "description": "Search the web. Primary: Tavily API. Fallback: OpenAI Responses web_search.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string"},
            }, "required": ["query"]},
        }, _web_search),
    ]
