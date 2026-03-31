# Bug Report: runtime is sending mixed low/medium requests to codex-lb

## Symptom

- User observed that the agent sends unexpected requests to local `codex-lb`.
- Investigation needed to answer:
  - what Ouroboros is trying to do right now
  - what it actually sends to `codex-lb`

## Phase 1: Root Cause

### Confirmed behavior

- Latest direct task in Ouroboros logs is task `558a85c9`.
- It is an auto-resume task after restart:
  - continue unfinished work on the raw-JSON final-response bug
  - respond in Russian
  - do one concrete next step first
- Tool activity for that task:
  - `repo_list .`
  - `repo_list tests`
  - `repo_list tests/e2e`
  - `repo_read ouroboros/loop.py`

### Current parallel activity

- Background consciousness is also active.
- It repeatedly reads:
  - `ouroboros/loop.py`
  - chat history
  - repo file listings
  - identity / consciousness files

### Request pattern in codex-lb

- `request_logs` in `codex-lb` show two distinct families:
  1. `gpt-5.1-codex-max` with `reasoning_effort=medium`
  2. `gpt-5.1-codex-max` with `reasoning_effort=low`
- The `medium` entries align exactly with task `558a85c9` timestamps.
- The `low` entries align with consciousness/helper calls.

## Phase 2: Pattern Analysis

### Matching logs

- Ouroboros event log for task `558a85c9`:
  - 17:51:11 round 1 `medium`
  - 17:51:15 round 2 `medium`
  - 17:51:19 round 3 `medium`
  - 17:51:23 round 4 `medium`
  - 17:51:28 round 5 `medium`
- codex-lb request logs in same window show matching `gpt-5.1-codex-max medium` requests with corresponding token sizes.

### Working explanation

- Main task loop starts with `initial_effort="medium"` for normal tasks.
- Background consciousness hardcodes `reasoning_effort="low"`.
- Relevance-rewrite and some helper flows also hardcode `low`.

## Phase 3: Hypothesis

### Hypothesis

The user is seeing `low` because they are looking at background/helper traffic, not only the main user task traffic.

### Test result

- Confirmed by timestamp correlation between:
  - `data/logs/events.jsonl`
  - `data/logs/tools.jsonl`
  - `data/logs/thinking_trace.jsonl`
  - `codex-lb` `request_logs`

## Phase 4: Root Cause Summary

- Ouroboros is currently trying to continue an unfinished self-fix task around raw JSON leaking into final replies.
- Its main task requests go out as:
  - model: `gpt-5.1-codex-max`
  - effort: `medium`
  - endpoint family: OpenAI chat completions via local `codex-lb`
- Separate background/helper requests go out as:
  - model: `gpt-5.1-codex-max`
  - effort: `low`

## Relevant code locations

- Main request construction: `/home/deicer/ouroboros/ouroboros/llm.py`
- Main task initial effort: `/home/deicer/ouroboros/ouroboros/agent.py`
- Background consciousness low effort: `/home/deicer/ouroboros/ouroboros/consciousness.py`
- Relevance rewrite low effort: `/home/deicer/ouroboros/ouroboros/agent.py`

