from __future__ import annotations

import json
import urllib.request

from supervisor.trace_web import start_trace_web_server


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_trace_web_endpoints(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    (logs / "thinking_trace.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-02-25T08:00:00+00:00",
                "source": "task_loop",
                "step": "llm_response",
                "task_id": "task-1",
                "round": 3,
                "details": {"assistant_preview": "hello"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-02-25T08:00:00+00:00",
                "type": "llm_round",
                "task_id": "task-1",
                "round": 3,
                "model": "gpt-5.4",
                "prompt_tokens": 1024,
                "completion_tokens": 128,
                "cached_tokens": 256,
                "cache_write_tokens": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "supervisor.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-02-25T08:00:00+00:00",
                "type": "main_loop_heartbeat",
                "workers_total": 5,
                "workers_alive": 5,
                "pending_count": 0,
                "running_count": 0,
                "event_q_size": 0,
                "spent_usd": 0.1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    server = start_trace_web_server(tmp_path, host="127.0.0.1", port=0)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        health = _read_json(f"{base}/healthz")
        assert health.get("ok") is True

        payload = _read_json(f"{base}/api/thinking?limit=10")
        assert payload.get("ok") is True
        assert payload.get("entries")
        assert payload["entries"][0]["source"] == "task_loop"
        assert payload["entries"][0]["details"]["prompt_tokens"] == 1024
        assert payload["entries"][0]["details"]["cached_tokens"] == 256
        assert payload["entries"][0]["details"]["cache_hit_rate_pct"] == 25.0
        assert payload.get("supervisor", {}).get("workers_alive") == 5

        with urllib.request.urlopen(f"{base}/thinking", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "cache " in html
    finally:
        server.shutdown()
        server.server_close()


def test_trace_web_collapses_duplicate_final_response_after_llm_response(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    preview = "Готово. Это итоговый ответ без tool calls."
    (logs / "thinking_trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-02-25T08:00:00+00:00",
                        "source": "task_loop",
                        "step": "llm_response",
                        "task_id": "task-1",
                        "round": 3,
                        "details": {"assistant_preview": preview, "tool_count": 0, "tool_names": []},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-02-25T08:00:01+00:00",
                        "source": "task_loop",
                        "step": "final_response",
                        "task_id": "task-1",
                        "round": 3,
                        "details": {"response_preview": preview},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "events.jsonl").write_text("", encoding="utf-8")
    (logs / "supervisor.jsonl").write_text("", encoding="utf-8")

    server = start_trace_web_server(tmp_path, host="127.0.0.1", port=0)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        payload = _read_json(f"{base}/api/thinking?limit=10")
        entries = payload.get("entries") or []
        assert len(entries) == 1
        assert entries[0]["step"] == "final_response"
        assert entries[0]["details"]["response_preview"] == preview
    finally:
        server.shutdown()
        server.server_close()


def test_trace_web_shows_cache_metrics_only_for_llm_steps(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    (logs / "thinking_trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-02-25T08:00:00+00:00",
                        "source": "task_loop",
                        "step": "llm_response",
                        "task_id": "task-1",
                        "round": 3,
                        "details": {"assistant_preview": "hello", "tool_count": 0, "tool_names": []},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-02-25T08:00:01+00:00",
                        "source": "task_loop",
                        "step": "tool_result",
                        "task_id": "task-1",
                        "round": 3,
                        "details": {"result_preview": "ok"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-02-25T08:00:00+00:00",
                "type": "llm_round",
                "task_id": "task-1",
                "round": 3,
                "model": "gpt-5.4",
                "prompt_tokens": 1024,
                "completion_tokens": 128,
                "cached_tokens": 256,
                "cache_write_tokens": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "supervisor.jsonl").write_text("", encoding="utf-8")

    server = start_trace_web_server(tmp_path, host="127.0.0.1", port=0)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        payload = _read_json(f"{base}/api/thinking?limit=10")
        entries = payload.get("entries") or []
        assert entries[0]["step"] == "llm_response"
        assert entries[0]["details"]["cache_hit_rate_pct"] == 25.0
        assert entries[1]["step"] == "tool_result"
        assert "cache_hit_rate_pct" not in (entries[1].get("details") or {})
    finally:
        server.shutdown()
        server.server_close()
