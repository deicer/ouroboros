"""
Ouroboros — LLM client.

The only module that communicates with the LLM API (OpenRouter).
Contract: chat(), default_model(), available_models(), add_usage().
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

def _env_model(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _env_model_list(name: str) -> List[str]:
    raw = _env_model(name)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _ordered_unique(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        v = str(item or "").strip()
        if not v or v in seen:
            continue
        out.append(v)
        seen.add(v)
    return out


def get_main_model_from_env() -> str:
    main = _env_model("OUROBOROS_MODEL")
    if not main:
        raise RuntimeError("OUROBOROS_MODEL is required and must be non-empty")
    return main


def get_light_model_from_env() -> str:
    light = _env_model("OUROBOROS_MODEL_LIGHT")
    return light or get_main_model_from_env()


def get_allowed_models_from_env() -> List[str]:
    main = _env_model("OUROBOROS_MODEL")
    code = _env_model("OUROBOROS_MODEL_CODE")
    light = _env_model("OUROBOROS_MODEL_LIGHT")
    fallback_models = _env_model_list("OUROBOROS_MODEL_FALLBACK_LIST")
    paid_models = _env_model_list("OUROBOROS_MODEL_PAID_LIST")
    free_models = _env_model_list("OUROBOROS_MODEL_FREE_LIST")
    return _ordered_unique([main, code, light, *paid_models, *free_models, *fallback_models])


def get_paid_models_from_env(active_model: str = "") -> List[str]:
    """
    Return paid model candidates from env in priority order.

    Priority:
    1) OUROBOROS_MODEL_PAID_LIST (explicit ordered list)
    2) Legacy model slots (main/code/light + fallback list), filtered to non-free
    """
    active = str(active_model or "").strip()
    explicit_models = _env_model_list("OUROBOROS_MODEL_PAID_LIST")
    if explicit_models:
        candidates = _ordered_unique(explicit_models)
        return [m for m in candidates if m and m != active and not is_free_model(m)]

    main = _env_model("OUROBOROS_MODEL")
    code = _env_model("OUROBOROS_MODEL_CODE")
    light = _env_model("OUROBOROS_MODEL_LIGHT")
    fallback_models = _env_model_list("OUROBOROS_MODEL_FALLBACK_LIST")
    candidates = _ordered_unique([main, code, light, *fallback_models])
    return [m for m in candidates if m and m != active and not is_free_model(m)]


def get_fallback_models_from_env(active_model: str = "") -> List[str]:
    active = str(active_model or "").strip()
    paid_models = get_paid_models_from_env(active_model=active)
    free_models = get_free_models_from_env(active_model=active)
    candidates = _ordered_unique([*paid_models, *free_models])
    return [m for m in candidates if m and m != active]


def is_free_model(model: str) -> bool:
    """Heuristic check for free-tier model identifiers."""
    m = str(model or "").strip().lower()
    if not m:
        return False
    return (
        m.endswith(":free")
        or m.endswith("-free")
        or m.endswith("/free")
        or ":free" in m
    )


def get_free_models_from_env(active_model: str = "") -> List[str]:
    """
    Return free model candidates from env in priority order.

    Priority:
    1) OUROBOROS_MODEL_FREE_LIST (explicit override)
    2) Free models discovered among fallback/main/code/light env models
    """
    active = str(active_model or "").strip()

    explicit_models = _env_model_list("OUROBOROS_MODEL_FREE_LIST")
    if explicit_models:
        candidates = _ordered_unique(explicit_models)
        # Explicit list is authoritative even if names do not contain "free".
        return [m for m in candidates if m and m != active]

    main = _env_model("OUROBOROS_MODEL")
    code = _env_model("OUROBOROS_MODEL_CODE")
    light = _env_model("OUROBOROS_MODEL_LIGHT")
    fallback_models = _env_model_list("OUROBOROS_MODEL_FALLBACK_LIST")
    candidates = _ordered_unique([*fallback_models, main, code, light])
    return [m for m in candidates if m and m != active and is_free_model(m)]


def resolve_model_from_env(requested_model: str = "") -> str:
    requested = str(requested_model or "").strip()
    main = get_main_model_from_env()
    allowed = get_allowed_models_from_env()
    if requested and requested in allowed:
        return requested
    if requested and requested != main:
        log.warning(
            "Blocked non-env model '%s'; using OUROBOROS_MODEL='%s'. Allowed: %s",
            requested,
            main,
            ", ".join(allowed) or "<empty>",
        )
    return main


def build_reasoning_config(model: str, reasoning_effort: str = "medium") -> Dict[str, Any]:
    """Build OpenRouter reasoning config for the selected model."""
    model_name = str(model or "").strip()
    effort = normalize_reasoning_effort(reasoning_effort, default="medium")

    # Grok 4.1 Fast: use the explicit reasoning.enabled toggle.
    if model_name.startswith("x-ai/grok-4.1-fast"):
        enabled_default = True
        enabled = _env_bool("OUROBOROS_REASONING_ENABLED", default=enabled_default)
        if effort == "none":
            enabled = False
        return {
            "enabled": enabled,
            "exclude": True,
        }

    # Generic OpenRouter-compatible reasoning control for other models.
    return {
        "effort": effort,
        "exclude": True,
    }


def normalize_reasoning_effort(value: str, default: str = "medium") -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def add_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate usage from one LLM call into a running total."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(usage.get(k) or 0)
    if usage.get("cost"):
        total["cost"] = float(total.get("cost") or 0) + float(usage["cost"])


def fetch_openrouter_pricing() -> Dict[str, Tuple[float, float, float]]:
    """
    Fetch current pricing from OpenRouter API.

    Returns dict of {model_id: (input_per_1m, cached_per_1m, output_per_1m)}.
    Returns empty dict on failure.
    """
    import logging
    log = logging.getLogger("ouroboros.llm")

    try:
        import requests
    except ImportError:
        log.warning("requests not installed, cannot fetch pricing")
        return {}

    try:
        url = "https://openrouter.ai/api/v1/models"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        models = data.get("data", [])

        # Prefixes we care about
        prefixes = ("anthropic/", "openai/", "google/", "meta-llama/", "x-ai/", "qwen/")

        pricing_dict = {}
        for model in models:
            model_id = model.get("id", "")
            if not model_id.startswith(prefixes):
                continue

            pricing = model.get("pricing", {})
            if not pricing or not pricing.get("prompt"):
                continue

            # OpenRouter pricing is in dollars per token (raw values)
            raw_prompt = float(pricing.get("prompt", 0))
            raw_completion = float(pricing.get("completion", 0))
            raw_cached_str = pricing.get("input_cache_read")
            raw_cached = float(raw_cached_str) if raw_cached_str else None

            # Convert to per-million tokens
            prompt_price = round(raw_prompt * 1_000_000, 4)
            completion_price = round(raw_completion * 1_000_000, 4)
            if raw_cached is not None:
                cached_price = round(raw_cached * 1_000_000, 4)
            else:
                cached_price = round(prompt_price * 0.1, 4)  # fallback: 10% of prompt

            # Sanity check: skip obviously wrong prices
            if prompt_price > 1000 or completion_price > 1000:
                log.warning(f"Skipping {model_id}: prices seem wrong (prompt={prompt_price}, completion={completion_price})")
                continue

            pricing_dict[model_id] = (prompt_price, cached_price, completion_price)

        log.info(f"Fetched pricing for {len(pricing_dict)} models from OpenRouter")
        return pricing_dict

    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning(f"Failed to fetch OpenRouter pricing: {e}")
        return {}


class LLMClient:
    """OpenRouter API wrapper. All LLM calls go through this class."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                default_headers={
                    "HTTP-Referer": "https://github.com/jkee/ouroboros",
                    "X-Title": "Ouroboros",
                },
            )
        return self._client

    def _fetch_generation_cost(self, generation_id: str) -> Optional[float]:
        """Fetch cost from OpenRouter Generation API as fallback."""
        try:
            import requests
            url = f"{self._base_url.rstrip('/')}/generation?id={generation_id}"
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
            # Generation might not be ready yet — retry once after short delay
            time.sleep(0.5)
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
        except Exception:
            log.debug("Failed to fetch generation cost from OpenRouter", exc_info=True)
            pass
        return None

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Single LLM call. Returns: (response_message_dict, usage_dict with cost)."""
        client = self._get_client()
        model = resolve_model_from_env(model)

        extra_body: Dict[str, Any] = {
            "reasoning": build_reasoning_config(model, reasoning_effort=reasoning_effort),
        }

        # Pin Anthropic models to Anthropic provider for prompt caching
        if model.startswith("anthropic/"):
            extra_body["provider"] = {
                "order": ["Anthropic"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": extra_body,
        }
        if tools:
            # Add cache_control to last tool for Anthropic prompt caching
            # This caches all tool schemas (they never change between calls)
            tools_with_cache = [t for t in tools]  # shallow copy
            if tools_with_cache:
                last_tool = {**tools_with_cache[-1]}  # copy last tool
                last_tool["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                tools_with_cache[-1] = last_tool
            kwargs["tools"] = tools_with_cache
            kwargs["tool_choice"] = tool_choice

        resp = client.chat.completions.create(**kwargs)
        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}

        # Extract cached_tokens from prompt_tokens_details if available
        if not usage.get("cached_tokens"):
            prompt_details = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details, dict) and prompt_details.get("cached_tokens"):
                usage["cached_tokens"] = int(prompt_details["cached_tokens"])

        # Extract cache_write_tokens from prompt_tokens_details if available
        # OpenRouter: "cache_write_tokens"
        # Native Anthropic: "cache_creation_tokens" or "cache_creation_input_tokens"
        if not usage.get("cache_write_tokens"):
            prompt_details_for_write = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details_for_write, dict):
                cache_write = (prompt_details_for_write.get("cache_write_tokens")
                              or prompt_details_for_write.get("cache_creation_tokens")
                              or prompt_details_for_write.get("cache_creation_input_tokens"))
                if cache_write:
                    usage["cache_write_tokens"] = int(cache_write)

        # Ensure cost is present in usage (OpenRouter includes it, but fallback if missing)
        if not usage.get("cost"):
            gen_id = resp_dict.get("id") or ""
            if gen_id:
                cost = self._fetch_generation_cost(gen_id)
                if cost is not None:
                    usage["cost"] = cost

        return msg, usage

    def vision_query(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        model: str = "",
        max_tokens: int = 1024,
        reasoning_effort: str = "low",
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send a vision query to an LLM. Lightweight — no tools, no loop.

        Args:
            prompt: Text instruction for the model
            images: List of image dicts. Each dict must have either:
                - {"url": "https://..."} — for URL images
                - {"base64": "<b64>", "mime": "image/png"} — for base64 images
            model: VLM-capable model ID
            max_tokens: Max response tokens
            reasoning_effort: Effort level

        Returns:
            (text_response, usage_dict)
        """
        # Build multipart content
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            if "url" in img:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"]},
                })
            elif "base64" in img:
                mime = img.get("mime", "image/png")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img['base64']}"},
                })
            else:
                log.warning("vision_query: skipping image with unknown format: %s", list(img.keys()))

        messages = [{"role": "user", "content": content}]
        resolved_model = resolve_model_from_env(model)
        response_msg, usage = self.chat(
            messages=messages,
            model=resolved_model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
        text = response_msg.get("content") or ""
        return text, usage

    def default_model(self) -> str:
        """Return the single default model from env. LLM switches via tool if needed."""
        return get_main_model_from_env()

    def available_models(self) -> List[str]:
        """Return list of available models from env (for switch_model tool schema)."""
        models = get_allowed_models_from_env()
        if not models:
            return [get_main_model_from_env()]
        return models
