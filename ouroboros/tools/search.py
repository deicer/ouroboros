"""Web search tool."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry


def _clean_secret_env(name: str) -> str:
    """Read secret-like env value and strip placeholder/comment artifacts."""
    raw = (os.environ.get(name, "") or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    return raw.split()[0]


def _to_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return []


def _parse_boolish(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None


def _to_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        v = int(str(value))
    except (TypeError, ValueError):
        v = default
    return max(min_value, min(max_value, v))


def _tavily_search(
    query: str,
    api_key: str,
    *,
    search_depth: str = "",
    topic: str = "",
    max_results: Optional[int] = None,
    time_range: str = "",
    start_date: str = "",
    end_date: str = "",
    include_domains: Any = None,
    exclude_domains: Any = None,
    country: str = "",
    include_answer: str = "",
    include_raw_content: str = "",
    include_images: Optional[bool] = None,
    include_image_descriptions: Optional[bool] = None,
    include_favicon: Optional[bool] = None,
    auto_parameters: Optional[bool] = None,
    chunks_per_source: Optional[int] = None,
) -> Dict[str, Any]:
    """Search web via Tavily API (primary backend)."""
    base_url = (os.environ.get("OUROBOROS_TAVILY_BASE_URL", "https://api.tavily.com") or "").rstrip("/")
    url = f"{base_url}/search"
    env_max = _to_int(os.environ.get("OUROBOROS_TAVILY_MAX_RESULTS", "5"), default=5, min_value=0, max_value=20)
    final_max_results = _to_int(max_results, default=env_max, min_value=0, max_value=20)
    final_search_depth = (search_depth or os.environ.get("OUROBOROS_TAVILY_SEARCH_DEPTH", "basic") or "basic").strip()
    final_topic = (topic or os.environ.get("OUROBOROS_TAVILY_TOPIC", "general") or "general").strip()
    final_include_answer = (include_answer or os.environ.get("OUROBOROS_TAVILY_INCLUDE_ANSWER", "basic") or "basic").strip()
    final_include_raw_content = (
        include_raw_content or os.environ.get("OUROBOROS_TAVILY_INCLUDE_RAW_CONTENT", "false") or "false"
    ).strip()

    include_domains_list = _to_str_list(include_domains)
    exclude_domains_list = _to_str_list(exclude_domains)

    payload = {
        "query": query,
        "search_depth": final_search_depth,
        "topic": final_topic,
        "max_results": final_max_results,
        "include_answer": final_include_answer,
        "include_raw_content": final_include_raw_content,
        "include_images": _parse_boolish(include_images) if include_images is not None else False,
        "include_image_descriptions": (
            _parse_boolish(include_image_descriptions) if include_image_descriptions is not None else False
        ),
        "include_favicon": _parse_boolish(include_favicon) if include_favicon is not None else True,
    }
    if time_range:
        payload["time_range"] = str(time_range).strip()
    if start_date:
        payload["start_date"] = str(start_date).strip()
    if end_date:
        payload["end_date"] = str(end_date).strip()
    if country:
        payload["country"] = str(country).strip()
    if include_domains_list:
        payload["include_domains"] = include_domains_list
    if exclude_domains_list:
        payload["exclude_domains"] = exclude_domains_list
    if auto_parameters is not None:
        parsed_auto = _parse_boolish(auto_parameters)
        if parsed_auto is not None:
            payload["auto_parameters"] = parsed_auto
    if chunks_per_source is not None:
        payload["chunks_per_source"] = _to_int(chunks_per_source, default=3, min_value=1, max_value=3)

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


def _web_search(
    ctx: ToolContext,
    query: str,
    search_depth: str = "",
    topic: str = "",
    max_results: Optional[int] = None,
    time_range: str = "",
    start_date: str = "",
    end_date: str = "",
    include_domains: Any = None,
    exclude_domains: Any = None,
    country: str = "",
    include_answer: str = "",
    include_raw_content: str = "",
    include_images: Optional[bool] = None,
    include_image_descriptions: Optional[bool] = None,
    include_favicon: Optional[bool] = None,
    auto_parameters: Optional[bool] = None,
    chunks_per_source: Optional[int] = None,
) -> str:
    tavily_key = _clean_secret_env("TAVILY_API_KEY")
    openai_key = _clean_secret_env("OPENAI_API_KEY")
    errors: List[str] = []

    # Primary backend: Tavily
    if tavily_key:
        try:
            result = _tavily_search(
                query=query,
                api_key=tavily_key,
                search_depth=search_depth,
                topic=topic,
                max_results=max_results,
                time_range=time_range,
                start_date=start_date,
                end_date=end_date,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                country=country,
                include_answer=include_answer,
                include_raw_content=include_raw_content,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
                include_favicon=include_favicon,
                auto_parameters=auto_parameters,
                chunks_per_source=chunks_per_source,
            )
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
            "description": "Search the web. Primary: Tavily API (recommended). Fallback: OpenAI Responses web_search.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string"},
                "search_depth": {"type": "string", "enum": ["ultra-fast", "fast", "basic", "advanced"]},
                "topic": {"type": "string", "enum": ["general", "news", "finance"]},
                "max_results": {"type": "integer", "minimum": 0, "maximum": 20},
                "time_range": {"type": "string", "enum": ["day", "week", "month", "year", "d", "w", "m", "y"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "include_domains": {"type": "array", "items": {"type": "string"}},
                "exclude_domains": {"type": "array", "items": {"type": "string"}},
                "country": {"type": "string", "description": "Country boost for general topic"},
                "include_answer": {"type": "string", "description": "false|true|basic|advanced"},
                "include_raw_content": {"type": "string", "description": "false|true|text|markdown"},
                "include_images": {"type": "boolean"},
                "include_image_descriptions": {"type": "boolean"},
                "include_favicon": {"type": "boolean"},
                "auto_parameters": {"type": "boolean"},
                "chunks_per_source": {"type": "integer", "minimum": 1, "maximum": 3},
            }, "required": ["query"]},
        }, _web_search),
    ]
