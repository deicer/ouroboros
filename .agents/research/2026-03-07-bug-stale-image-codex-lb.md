# Bug Report: stale Docker image sends incompatible local-API payloads

## Symptom

- The `ouroboros` Docker service was restarted to apply `.env`, but the agent either:
  - sent bad requests to the local `codex-lb` API, or
  - crashed on startup with `ImportError: cannot import name 'get_code_model_from_env'`.

## Phase 1: Root Cause

### Reproduction

- `docker compose ps -a` showed `ouroboros` exiting after recreate.
- `docker compose logs ouroboros` showed:
  - `ImportError: cannot import name 'get_code_model_from_env' from 'ouroboros.llm'`
- Direct checks against the built image showed `/app/ouroboros/llm.py` did **not** contain `get_code_model_from_env`.
- Direct requests to `codex-lb` confirmed that `reasoning.exclude=true` breaks the local API:
  - response: `400 Unknown parameter: 'reasoning.exclude'.`

### Exact location

- Old image contents: `/app/ouroboros/llm.py`
- Startup path:
  - `/home/deicer/ouroboros/launcher.py`
  - `/home/deicer/ouroboros/supervisor/git_ops.py`
  - `/home/deicer/ouroboros/supervisor/workers.py`
  - `/home/deicer/ouroboros/ouroboros/loop.py`
  - `/home/deicer/ouroboros/ouroboros/llm.py`

### What is wrong

The built Docker image `ouroboros-ouroboros:latest` is stale. It predates recent local-LLM fixes and still contains old `ouroboros/llm.py` behavior:

- no `get_code_model_from_env()`
- `_is_local_base_url()` does not treat `host.docker.internal` as local
- `build_reasoning_config()` always adds `exclude=True`

That stale image is incompatible with the current branch and with `codex-lb`.

### When introduced

- Image was built from an older tree (`ed56774` observed in container logs/image contents).
- Relevant fixes landed later:
  - `6af77c0` added `get_code_model_from_env`
  - `06938a6` hardened local LLM bootstrap and payload handling

## Phase 2: Pattern Analysis

### Broken image behavior

- `docker run --rm --entrypoint sh ouroboros-ouroboros -lc "sed -n '300,420p' /app/ouroboros/llm.py"`
- showed `build_reasoning_config()` always returning `{"effort": ..., "exclude": True}`

### Working workspace behavior

- `/home/deicer/ouroboros/ouroboros/llm.py`
- current code adds `exclude=True` only when using real OpenRouter budget mode
- current code treats `host.docker.internal` as local

## Phase 3: Hypothesis

### Hypothesis

I think the agent breaks `codex-lb` because the running container uses an old image whose `llm.py` still sends OpenRouter-only payload fields to the local API and is missing newer startup-compatible symbols.

### Test

- OpenAI SDK call to `http://127.0.0.1:3455/v1/chat/completions` with:
  - `extra_body={"reasoning": {"effort": "medium"}}` -> OK
  - `extra_body={"reasoning": {"effort": "medium", "exclude": True}}` -> 400

### Result

- Hypothesis confirmed.

## Phase 4: Fix

### Root fix

- Rebuild the Docker image from the current workspace before starting/recreating the service.

### Verification target

- New image must contain:
  - `get_code_model_from_env` in `/app/ouroboros/llm.py`
  - `_is_local_base_url()` including `host.docker.internal`
  - `build_reasoning_config()` that does not send `exclude=True` for local base URLs

## Failure count

- 0 countable hypothesis failures

