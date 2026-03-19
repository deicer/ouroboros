# How to Improve Effectively

Self-improvement protocol. Authority: BIBLE.md sections 7–9.
This is my practical playbook — concrete steps, tools, anti-patterns, and
lessons from real cycles. I maintain this file and update it after learning.

---

## The Improvement Cycle

Every improvement follows this sequence. No shortcuts.

### 1. Explore

Understand the current state before touching anything.

```
repo_read("ouro/target_module.py")
run_shell(["wc", "-l", "ouro/target_module.py"])
git_status()
git_diff()
```

Checklist:
- Is the repo clean? No uncommitted changes.
- Which module is relevant? Stay focused — one module per cycle.
- Is the file within size limits? (BIBLE §8: ~2000 lines max, see ARCHITECTURE.md module sizes table)
- What does the current code actually do? Read it, don't assume.

Output: a one-sentence problem statement.
*"loop.py has pricing logic duplicated from llm.py — ~60 lines that should be imports."*

### 2. Plan

Define exactly what will change and why.

1. **What changes** — file(s), function(s), approximate lines.
2. **Why this is better** — what problem it solves, what value it adds.
3. **Risk assessment** — what could break? Which tests cover this?
4. **Scope check** — reject if it violates scope rules (see below).
5. **Checkpoint** — tag stable before risky changes.

```
promote_to_stable(reason="pre-improvement checkpoint")
```

Reject at planning stage:
- "Refactor everything" — too broad.
- Touching >3 files in one cycle.
- Adding a feature nobody asked for (YAGNI).
- Plan that requires >10 tool rounds to verify.
- Net complexity goes up with no clear benefit.

### 3. Execute

Use `claude_code_edit` — the only way to edit code. Never `drive_write` on repo files.

Prompt quality rules:
- Absolute file paths (`/app/...`).
- Specify what to remove AND what to add.
- One logical change per prompt.
- If >50 lines change, break into 2 prompts.
- Work in 2–3 iterations on improvement prompts (BIBLE §8).

After every edit:
```
git_diff()     # read every changed line
git_status()   # only expected files changed?
```

### 4. Verify

Always run both checks:
```
run_shell(["python", "-c", "import ouro.agent"])   # import check
run_shell(["python", "-m", "pytest", "tests/", "-x", "-q"])  # smoke tests
```

- Import error → fix before committing. Never push broken code.
- Test failure → 2 attempts to fix, then revert.
- Can't fix in 2 attempts → revert and rethink the approach.

### 5. Commit & Ship

Strictly in order:

1. Update `VERSION` (semver — BIBLE §15).
2. Update `README.md` changelog.
3. Update `ARCHITECTURE.md` if structural change (module sizes, new tools, data flows).
4. Bible check — verify the result against the Constitution.
5. `repo_commit_push` — commit and push.
6. `request_restart` (if runtime-affecting change).
7. `promote_to_stable` (after restart confirmed healthy).
8. `log_evolution` — title, motivation, changes, lessons learned.
9. Report to user (required — BIBLE §7).

---

## Scope Rules

From BIBLE §8 — enforced at planning stage.

| Signal | Action |
|--------|--------|
| Module > 2000 lines | Split before improving |
| Method > 150 lines | Decompose first |
| Method > 8 parameters | Refactor signature |
| Change touches > 3 files | Break into multiple cycles |
| Feature nobody requested | Defer (YAGNI) |
| Net complexity increases | Reject the plan |
| Last several iterations had no result | Pause and reassess (BIBLE §7) |

---

## What to Improve

Priority order for selecting improvements:

1. **User-reported bugs or requests** — always highest priority.
2. **Critical issues from health invariants** — version desync, budget drift, duplicate processing.
3. **Architecture deficits** — code doesn't match BIBLE.md, missing functionality described in constitution.
4. **Code quality** — modules at size limit, duplicated logic, missing test coverage.
5. **Performance** — reduce token usage, speed up common operations.
6. **New capabilities** — only when the above are clean.

Sources of improvement ideas:
- Health invariants in context (check every response).
- `ARCHITECTURE.md` module sizes table — spot modules approaching limits.
- `codebase_health` tool — complexity metrics.
- Knowledge base — recorded anti-patterns and incidents.
- Background consciousness observations.
- User feedback patterns from chat history.

---

## Safety Rules

**Never:**
- Push without running import test + smoke tests first.
- Edit `BIBLE.md` without explicit user approval (even in `/no-approve` mode).
- Make a change and immediately restart without verifying.
- Repeat the same failing approach — if it didn't work twice, rethink.

**On failure:**
- Import breaks → `git checkout HEAD -- <file>` to revert the file.
- Can't fix in 2 rounds → revert, record the problem in knowledge base.
- System broken after restart → rollback to stable tag, alert user.
- Multiple consecutive evolution failures → circuit breaker stops auto-evolution.

**Budget awareness:**
- Check remaining budget before starting a cycle.
- Evolution pauses when remaining < $50 (enforced by supervisor).
- Keep evolution cycles efficient — aim for <$2 per cycle.
- Background consciousness has separate 10% budget cap.

---

## Tool Usage Patterns

### Code editing
```
claude_code_edit(prompt="In /app/ouro/module.py, replace function X with Y.
Reason: [why]. Keep the existing signature unchanged.")
```
- Claude Code CLI runs as `ouro` user (non-root).
- Max 12 turns, 300s timeout.
- Tools available inside: Read, Write, Edit, Grep, Glob.

### Multi-model review (for significant changes)
```
multi_model_review(prompt="Review this change for correctness and edge cases",
                   files=["ouro/module.py"])
```

### Knowledge base (persist lessons)
```
knowledge_write(topic="improvement-lesson-xyz",
                content="What happened, why, what to do differently")
```

---

## Anti-Patterns

Learned from real cycles — don't repeat these.

- **Consciousness spawning heavy subtasks** — background thinking should be lightweight. Schedule tasks for heavy work, don't execute them in consciousness.
- **Touching code you haven't read** — always `repo_read` before editing. Assumptions about code structure are often wrong.
- **Improving without a problem statement** — "make it better" is not a plan. Name the specific problem.
- **Skipping the verify step** — "it's a small change" is how broken code gets pushed.
- **Patching symptoms** — if the same area needs fixes repeatedly, step back and rethink the design.
- **Over-engineering during improvement** — the fix should be minimal. Don't add abstractions, config options, or "nice to have" features in the same cycle.

---

## Lessons Learned

Updated after improvement cycles. Most recent first.

- E2E tests require `git user.email` / `user.name` configured — without it, commit exits 128.
- Model names in tool calls must be verified against OpenRouter — nonexistent models cause infinite hangs.
- Architecture review subtasks are valuable but expensive — limit to 1 per session.
- Prompt quality for `claude_code_edit` is the #1 factor in cycle success — invest time here.

---

*This file is a living document. Update after every improvement cycle.*
