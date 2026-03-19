"""Composio integration — external app actions via OAuth (Gmail, Slack, GitHub, etc.)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-init singleton (thread-safe)
# ---------------------------------------------------------------------------

_toolset = None
_toolset_lock = threading.Lock()


def _get_toolset():
    """Return cached ComposioToolSet. Thread-safe lazy init, reads current env key."""
    global _toolset
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError(
            "COMPOSIO_API_KEY not set. Get your key at https://composio.dev and add it to .env"
        )
    if _toolset is None:
        with _toolset_lock:
            if _toolset is None:
                from composio import ComposioToolSet
                _toolset = ComposioToolSet(api_key=api_key)
    return _toolset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_issue_create(ctx: ToolContext, title: str, body: str, labels: str = "") -> str:
    """Create a GitHub issue via `gh` CLI. Returns URL or error string."""
    args = ["gh", "issue", "create", f"--title={title}"]
    if labels:
        args.append(f"--label={labels}")
    args.append("--body-file=-")
    try:
        res = subprocess.run(
            args,
            cwd=str(ctx.repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
            input=body,
        )
        if res.returncode != 0:
            err = (res.stderr or "").strip()
            return f"⚠️ GH_ERROR: {err.split(chr(10))[0][:200]}"
        return res.stdout.strip()
    except FileNotFoundError:
        return "⚠️ GH_ERROR: `gh` CLI not found."
    except subprocess.TimeoutExpired:
        return "⚠️ GH_TIMEOUT: exceeded 30s."
    except Exception as e:
        return f"⚠️ GH_ERROR: {e}"


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _list_connections(ctx: ToolContext, entity_id: str = "default") -> str:
    """List all active Composio app connections for the entity."""
    try:
        toolset = _get_toolset()
        entity = toolset.get_entity(id=entity_id)
        connections = entity.get_connections()
        if not connections:
            return "No apps connected. Use composio_get_oauth_url to connect an app."
        items = []
        for conn in connections:
            items.append({
                "app": getattr(conn, "appUniqueId", str(conn)),
                "status": getattr(conn, "status", "unknown"),
                "id": getattr(conn, "id", ""),
            })
        return json.dumps(items, indent=2)
    except RuntimeError as e:
        return f"⚠️ {e}"
    except Exception as e:
        return f"⚠️ COMPOSIO_ERROR: {e}"


def _get_oauth_url(ctx: ToolContext, app: str, entity_id: str = "default") -> str:
    """Generate OAuth URL to connect a new app."""
    try:
        toolset = _get_toolset()
        entity = toolset.get_entity(id=entity_id)
        # Composio App enum uses uppercase names
        from composio import App
        try:
            app_enum = App(app.upper())
        except (ValueError, KeyError):
            app_enum = app.upper()
        request = entity.initiate_connection(app=app_enum)
        url = getattr(request, "redirectUrl", None) or getattr(request, "redirect_url", None)
        if url:
            return f"OK: Open this URL to authorize {app}:\n{url}"
        return f"OK: Connection initiated for {app}. Check Composio dashboard for status."
    except RuntimeError as e:
        return f"⚠️ {e}"
    except Exception as e:
        err = str(e)
        if "not supported" in err.lower() or "not found" in err.lower():
            return (
                f"⚠️ App '{app}' may not be available in your Composio project. "
                f"Use composio_request_app to ask the project creator to enable it."
            )
        return f"⚠️ COMPOSIO_ERROR: {e}"


def _run_action(ctx: ToolContext, action: str, params: Optional[Dict[str, Any]] = None,
                entity_id: str = "default") -> str:
    """Execute a Composio action (e.g. GMAIL_FETCH_EMAILS, GITHUB_LIST_ISSUES)."""
    try:
        toolset = _get_toolset()
        from composio import Action
        try:
            action_enum = Action(action.upper())
        except (ValueError, KeyError):
            action_enum = action.upper()
        result = toolset.execute_action(
            action=action_enum,
            params=params or {},
            entity_id=entity_id,
        )
        # Result is usually a dict
        if isinstance(result, dict):
            return json.dumps(result, indent=2, default=str)
        return str(result)
    except RuntimeError as e:
        return f"⚠️ {e}"
    except Exception as e:
        err = str(e)
        if "not connected" in err.lower() or "no connection" in err.lower():
            return (
                f"⚠️ App not connected for this action. "
                f"Call composio_get_oauth_url with the app name to authorize it first."
            )
        return f"⚠️ COMPOSIO_ERROR: {e}"


def _request_app(ctx: ToolContext, app: str, reason: str) -> str:
    """Create a GitHub issue requesting the project creator to enable an app in Composio."""
    try:
        title = f"[Composio] Request to connect app: {app.upper()}"
        body = (
            f"## Composio App Connection Request\n\n"
            f"**App:** {app.upper()}\n"
            f"**Reason:** {reason}\n\n"
            f"The agent needs access to **{app}** via Composio but it is not currently "
            f"enabled in the project.\n\n"
            f"**Action required:** Project creator (Viktor Tarnavskii) needs to:\n"
            f"1. Go to https://composio.dev dashboard\n"
            f"2. Enable the {app.upper()} integration\n"
            f"3. Close this issue when done\n"
        )
        result = _gh_issue_create(ctx, title=title, body=body, labels="composio-app-request")
        if result.startswith("⚠️"):
            return result
        return (
            f"✅ Issue created: {result}\n\n"
            f"Please ask the project creator (Viktor Tarnavskii) to enable {app.upper()} "
            f"in the Composio dashboard."
        )
    except Exception as e:
        return f"⚠️ COMPOSIO_ERROR: Failed to create issue: {e}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("composio_list_connections", {
            "name": "composio_list_connections",
            "description": (
                "List all active Composio app connections (Gmail, Slack, GitHub, etc.). "
                "Shows which external apps are authorized and available for use."
            ),
            "parameters": {"type": "object", "properties": {
                "entity_id": {"type": "string", "default": "default",
                              "description": "Composio entity ID (default for single-user)"},
            }, "required": []},
        }, _list_connections),

        ToolEntry("composio_get_oauth_url", {
            "name": "composio_get_oauth_url",
            "description": (
                "Generate an OAuth authorization URL to connect a new app via Composio. "
                "User opens the URL, completes OAuth, and the app is connected permanently. "
                "Examples: GMAIL, GITHUB, SLACK, NOTION, LINEAR."
            ),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "App name (e.g. GMAIL, SLACK, GITHUB)"},
                "entity_id": {"type": "string", "default": "default",
                              "description": "Composio entity ID"},
            }, "required": ["app"]},
        }, _get_oauth_url),

        ToolEntry("composio_run_action", {
            "name": "composio_run_action",
            "description": (
                "Execute a Composio action on a connected app. "
                "Examples: GMAIL_FETCH_EMAILS, GMAIL_SEND_EMAIL, GITHUB_LIST_ISSUES, "
                "SLACK_SEND_MESSAGE, NOTION_CREATE_PAGE. "
                "The app must be connected first via composio_get_oauth_url."
            ),
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string",
                           "description": "Action name (e.g. GMAIL_FETCH_EMAILS)"},
                "params": {"type": "object", "default": {},
                           "description": "Action parameters (varies by action)"},
                "entity_id": {"type": "string", "default": "default",
                              "description": "Composio entity ID"},
            }, "required": ["action"]},
        }, _run_action, timeout_sec=180),

        ToolEntry("composio_request_app", {
            "name": "composio_request_app",
            "description": (
                "Request the project creator to enable a new app in Composio. "
                "Creates a GitHub issue with the request. Use when an app is not available "
                "in the Composio project and needs to be enabled by the owner."
            ),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "App name to request"},
                "reason": {"type": "string", "description": "Why this app is needed"},
            }, "required": ["app", "reason"]},
        }, _request_app),
    ]
