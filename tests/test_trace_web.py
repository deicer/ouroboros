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
                "details": {"assistant_preview": "hello"},
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
        assert payload.get("supervisor", {}).get("workers_alive") == 5
    finally:
        server.shutdown()
        server.server_close()

