from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ouroboros.tools.registry import ToolContext
from ouroboros.tools.search import _web_search


class _FakeResponse:
    def __init__(self, body: dict[str, Any]):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._raw


def _ctx() -> ToolContext:
    return ToolContext(repo_dir=Path("."), drive_root=Path("."))


def test_web_search_tavily_request_payload(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({
            "query": "ouroboros",
            "answer": "ok",
            "results": [{"title": "A", "url": "https://example.com", "content": "C", "score": 0.9}],
        })

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("ouroboros.tools.search.urllib.request.urlopen", fake_urlopen)

    raw = _web_search(
        _ctx(),
        query="ouroboros",
        search_depth="advanced",
        topic="news",
        max_results=7,
        time_range="week",
        include_domains=["docs.tavily.com"],
        exclude_domains=["spam.example"],
        include_images=True,
        auto_parameters=True,
        chunks_per_source=2,
    )
    payload = json.loads(raw)

    assert payload["backend"] == "tavily"
    assert captured["url"].endswith("/search")
    assert captured["headers"]["authorization"] == "Bearer tvly-test-key"
    assert captured["payload"]["query"] == "ouroboros"
    assert captured["payload"]["search_depth"] == "advanced"
    assert captured["payload"]["topic"] == "news"
    assert captured["payload"]["max_results"] == 7
    assert captured["payload"]["time_range"] == "week"
    assert captured["payload"]["include_domains"] == ["docs.tavily.com"]
    assert captured["payload"]["exclude_domains"] == ["spam.example"]
    assert captured["payload"]["include_images"] is True
    assert captured["payload"]["auto_parameters"] is True
    assert captured["payload"]["chunks_per_source"] == 2


def test_web_search_ignores_placeholder_env_keys(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "# put key here")
    monkeypatch.setenv("OPENAI_API_KEY", "# put key here")

    called = {"openai": False}

    def fake_openai(*args, **kwargs):
        called["openai"] = True
        return {"backend": "openai", "answer": "should not be used"}

    monkeypatch.setattr("ouroboros.tools.search._openai_web_search", fake_openai)
    raw = _web_search(_ctx(), query="test query")
    payload = json.loads(raw)

    assert called["openai"] is False
    assert "No search backend configured" in payload["error"]
