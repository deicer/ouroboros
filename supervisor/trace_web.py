"""Live web monitor for thinking_trace.jsonl."""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _to_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        v = int(str(raw))
    except Exception:
        v = default
    if v < minimum:
        return minimum
    if v > maximum:
        return maximum
    return v


def _tail_lines(path: pathlib.Path, limit: int, max_bytes: int = 2_000_000) -> List[str]:
    if limit <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size <= 0:
                return []

            block_size = 4096
            pos = file_size
            data = b""
            while pos > 0 and data.count(b"\n") <= limit and len(data) < max_bytes:
                read_size = min(block_size, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data

            lines = data.splitlines()[-limit:]
            return [ln.decode("utf-8", errors="replace") for ln in lines if ln.strip()]
    except Exception:
        return []


def _tail_jsonl(path: pathlib.Path, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _read_thinking(drive_root: pathlib.Path, limit: int, since: str = "") -> Tuple[List[Dict[str, Any]], str]:
    path = drive_root / "logs" / "thinking_trace.jsonl"
    entries = _tail_jsonl(path, max(limit * 4, 400))
    if since:
        entries = [e for e in entries if str(e.get("ts") or "") > since]
    entries = entries[-limit:]
    latest_ts = str(entries[-1].get("ts") or "") if entries else ""
    return entries, latest_ts


def _latest_supervisor(drive_root: pathlib.Path) -> Dict[str, Any]:
    sup_path = drive_root / "logs" / "supervisor.jsonl"
    items = _tail_jsonl(sup_path, 10)
    for obj in reversed(items):
        if str(obj.get("type") or "") == "main_loop_heartbeat":
            return {
                "ts": obj.get("ts"),
                "workers_total": obj.get("workers_total"),
                "workers_alive": obj.get("workers_alive"),
                "pending_count": obj.get("pending_count"),
                "running_count": obj.get("running_count"),
                "running_task_ids": obj.get("running_task_ids") or [],
                "event_q_size": obj.get("event_q_size"),
                "spent_usd": obj.get("spent_usd"),
            }
    return {}


def _page_html() -> str:
    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ouroboros Thinking Trace</title>
  <style>
    :root {
      --bg: #f4f2ec;
      --ink: #1f2a33;
      --muted: #607280;
      --card: #ffffff;
      --line: #d8ddd9;
      --accent: #0f766e;
      --accent-2: #c2410c;
      --warn: #b91c1c;
      --ok: #166534;
      --mono: "JetBrains Mono","IBM Plex Mono","Source Code Pro","SFMono-Regular",Consolas,monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--mono);
      color: var(--ink);
      background: radial-gradient(1200px 600px at 20% -20%, #d9f2ef, transparent), var(--bg);
    }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 20px; }
    .top {
      position: sticky; top: 0; z-index: 30;
      background: color-mix(in srgb, var(--bg) 92%, white);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      backdrop-filter: blur(4px);
    }
    .title { font-size: 18px; font-weight: 700; letter-spacing: .2px; }
    .subtitle { margin-top: 4px; color: var(--muted); font-size: 12px; }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .chip {
      font-size: 12px; border: 1px solid var(--line); border-radius: 999px;
      padding: 5px 10px; background: #fff;
    }
    .chip.ok { border-color: #b7e4c7; color: var(--ok); }
    .chip.warn { border-color: #f3c4c4; color: var(--warn); }
    .controls {
      margin-top: 12px; display: grid; gap: 8px;
      grid-template-columns: 160px 170px minmax(220px, 1fr) minmax(220px, 1fr);
    }
    button, input, select {
      border: 1px solid var(--line); border-radius: 10px;
      padding: 9px 10px; font: inherit; background: #fff;
    }
    button {
      background: linear-gradient(135deg, #0f766e, #0e9f8f);
      color: #fff; border: none; cursor: pointer; font-weight: 600;
    }
    button[data-paused="true"] { background: linear-gradient(135deg, #9f1239, #be123c); }
    .list { margin-top: 14px; display: grid; gap: 10px; }
    .item {
      border: 1px solid var(--line);
      border-left: 4px solid var(--accent);
      border-radius: 12px;
      background: var(--card);
      padding: 12px;
      animation: in .22s ease-out;
    }
    .item.consciousness { border-left-color: #0891b2; }
    .item.task_loop { border-left-color: #0f766e; }
    .item.review { border-left-color: #7c3aed; }
    @keyframes in { from { transform: translateY(6px); opacity: .2; } to { transform: none; opacity: 1; } }
    .head { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .badge {
      font-size: 11px;
      border-radius: 999px;
      padding: 3px 8px;
      background: #eef5f5;
      color: #0f4f49;
      border: 1px solid #cae1de;
    }
    .ts { font-size: 12px; color: var(--muted); margin-left: auto; }
    .preview {
      margin-top: 8px;
      white-space: pre-wrap;
      line-height: 1.45;
      font-size: 12px;
      color: #1b2d3a;
    }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: var(--accent-2); font-size: 12px; }
    pre {
      margin: 8px 0 0;
      background: #faf9f5;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      font-size: 11px;
      overflow: auto;
      max-height: 280px;
    }
    .footer {
      margin-top: 12px; color: var(--muted); font-size: 12px;
      display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap;
    }
    @media (max-width: 920px) {
      .controls { grid-template-columns: 1fr 1fr; }
      .ts { margin-left: 0; }
    }
    @media (max-width: 640px) {
      .controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="title">Ouroboros: live thinking trace</div>
      <div class="subtitle">Реальное время из <code>data/logs/thinking_trace.jsonl</code></div>
      <div id="chips" class="chips"></div>
      <div class="controls">
        <button id="toggleBtn" data-paused="false">Пауза</button>
        <select id="sourceFilter">
          <option value="">Все source</option>
        </select>
        <input id="stepFilter" placeholder="Фильтр по step (например: llm_response)">
        <input id="textFilter" placeholder="Поиск по превью/деталям">
      </div>
    </div>
    <div id="list" class="list"></div>
    <div class="footer">
      <div id="stats">entries: 0</div>
      <div id="clock"></div>
    </div>
  </div>

  <script>
    const listEl = document.getElementById("list");
    const chipsEl = document.getElementById("chips");
    const statsEl = document.getElementById("stats");
    const clockEl = document.getElementById("clock");
    const toggleBtn = document.getElementById("toggleBtn");
    const sourceFilter = document.getElementById("sourceFilter");
    const stepFilter = document.getElementById("stepFilter");
    const textFilter = document.getElementById("textFilter");

    const state = {
      paused: false,
      since: "",
      entries: [],
      seen: new Set(),
      supervisor: {},
      error: "",
      limit: 200
    };

    function esc(s) {
      return String(s ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }

    function entryKey(e) {
      const d = e.details || {};
      return [e.ts, e.source, e.step, d.task_id || "", d.round || "", d.tool || "", d.result_preview || ""].join("|");
    }

    function selectPreview(d) {
      return d.assistant_preview || d.response_preview || d.result_preview || "";
    }

    function updateSourceOptions() {
      const seen = new Set([""]);
      for (const e of state.entries) seen.add(e.source || "");
      const selected = sourceFilter.value;
      sourceFilter.innerHTML = "";
      [...seen].sort().forEach(v => {
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v ? `source: ${v}` : "Все source";
        sourceFilter.appendChild(opt);
      });
      sourceFilter.value = selected;
    }

    function matchFilters(e) {
      const src = sourceFilter.value.trim();
      const step = stepFilter.value.trim().toLowerCase();
      const q = textFilter.value.trim().toLowerCase();
      if (src && (e.source || "") !== src) return false;
      if (step && !String(e.step || "").toLowerCase().includes(step)) return false;
      if (q) {
        const d = e.details || {};
        const hay = `${e.source || ""} ${e.step || ""} ${selectPreview(d)} ${JSON.stringify(d)}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }

    function renderChips() {
      const sup = state.supervisor || {};
      const alive = Number(sup.workers_alive || 0);
      const total = Number(sup.workers_total || 0);
      const running = Number(sup.running_count || 0);
      const pending = Number(sup.pending_count || 0);
      const q = Number(sup.event_q_size || 0);
      const spent = sup.spent_usd;
      const ok = total > 0 && alive === total;
      const errorChip = state.error
        ? `<span class="chip warn">api: ${esc(state.error)}</span>`
        : `<span class="chip ok">api: ok</span>`;
      chipsEl.innerHTML = `
        <span class="chip ${ok ? "ok" : "warn"}">workers: ${alive}/${total}</span>
        <span class="chip">running: ${running}</span>
        <span class="chip">pending: ${pending}</span>
        <span class="chip">event_q: ${q}</span>
        <span class="chip">spent: ${spent == null ? "n/a" : "$" + Number(spent).toFixed(3)}</span>
        ${errorChip}
      `;
    }

    function render() {
      const items = state.entries.filter(matchFilters).slice().reverse();
      const html = items.map((e) => {
        const d = e.details || {};
        const preview = selectPreview(d);
        const klass = esc(e.source || "unknown");
        const round = d.round || e.round || "";
        const taskId = d.task_id || e.task_id || "";
        const tool = d.tool || "";
        return `
          <article class="item ${klass}">
            <div class="head">
              <span class="badge">${esc(e.source || "unknown")}</span>
              <span class="badge">${esc(e.step || "step")}</span>
              ${round ? `<span class="badge">round ${esc(round)}</span>` : ""}
              ${taskId ? `<span class="badge">task ${esc(taskId)}</span>` : ""}
              ${tool ? `<span class="badge">tool ${esc(tool)}</span>` : ""}
              <span class="ts">${esc(e.ts || "")}</span>
            </div>
            ${preview ? `<div class="preview">${esc(preview)}</div>` : ""}
            <details>
              <summary>details</summary>
              <pre>${esc(JSON.stringify(d, null, 2))}</pre>
            </details>
          </article>
        `;
      }).join("");
      listEl.innerHTML = html || `<article class="item"><div class="preview">Пока нет записей под текущие фильтры.</div></article>`;
      statsEl.textContent = `entries: ${state.entries.length} (показано: ${items.length})`;
      clockEl.textContent = `updated: ${new Date().toLocaleTimeString()}`;
      renderChips();
    }

    async function poll() {
      if (state.paused) return;
      try {
        const res = await fetch(`/api/thinking?limit=${state.limit}&since=${encodeURIComponent(state.since)}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.error = "";
        state.supervisor = data.supervisor || {};
        const entries = Array.isArray(data.entries) ? data.entries : [];
        for (const e of entries) {
          const key = entryKey(e);
          if (state.seen.has(key)) continue;
          state.seen.add(key);
          state.entries.push(e);
        }
        if (state.entries.length > 1200) {
          state.entries = state.entries.slice(-1200);
          state.seen = new Set(state.entries.map(entryKey));
        }
        if (data.latest_ts) state.since = data.latest_ts;
        updateSourceOptions();
        render();
      } catch (err) {
        state.error = String(err && err.message ? err.message : err);
        renderChips();
      }
    }

    toggleBtn.addEventListener("click", () => {
      state.paused = !state.paused;
      toggleBtn.textContent = state.paused ? "Продолжить" : "Пауза";
      toggleBtn.dataset.paused = state.paused ? "true" : "false";
      renderChips();
    });
    sourceFilter.addEventListener("change", render);
    stepFilter.addEventListener("input", render);
    textFilter.addEventListener("input", render);

    setInterval(poll, 1200);
    poll();
  </script>
</body>
</html>
"""


def start_trace_web_server(drive_root: pathlib.Path, host: str = "0.0.0.0", port: int = 8088) -> ThreadingHTTPServer:
    root = pathlib.Path(drive_root).resolve()

    class _Handler(BaseHTTPRequestHandler):
        server_version = "OuroborosTraceWeb/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, html: str, status: int = 200) -> None:
            raw = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path or "/"
            query = parse_qs(parsed.query or "")

            if route in ("/", "/thinking"):
                self._send_html(_page_html())
                return

            if route in ("/api/health", "/healthz"):
                self._send_json({"ok": True, "ts": _now_iso()})
                return

            if route == "/api/thinking":
                limit = _to_int((query.get("limit") or ["200"])[0], default=200, minimum=10, maximum=800)
                since = str((query.get("since") or [""])[0] or "")
                entries, latest_ts = _read_thinking(root, limit=limit, since=since)
                self._send_json({
                    "ok": True,
                    "ts": _now_iso(),
                    "entries": entries,
                    "latest_ts": latest_ts,
                    "supervisor": _latest_supervisor(root),
                })
                return

            self._send_json({"ok": False, "error": "not_found", "path": route}, status=404)

    httpd = ThreadingHTTPServer((host, int(port)), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="trace-web")
    thread.start()
    return httpd

