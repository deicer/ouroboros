# How to Improve Effectively

This file captures lessons on self-improvement.
Maintained by the agent. See BIBLE.md section 8.

## 2026-02-25 — Lessons from runtime monitoring

- Repeating status templates is harmful.
  - Symptom: same "фактологичный апдейт" was sent to different user questions.
  - Lesson: before replying, verify relevance to the latest user message; if response looks like stale template, force one focused re-answer.

- Background consciousness must be goal-constrained.
  - Symptom: multiple 5-round cycles with repetitive file reads and empty thought output.
  - Lesson: cap background tool usage and require one concrete action item or end cycle early.

- Context for short direct chat must be compact.
  - Symptom: ~29k prompt tokens for simple questions, leading to stale/inert replies.
  - Lesson: use lightweight context profile for short owner messages.

- Legacy path artifacts create false debugging narratives.
  - Symptom: `data/data/` still appears in listings and confuses diagnosis.
  - Lesson: run one-time cleanup/migration and keep canonical paths explicit in prompts.
