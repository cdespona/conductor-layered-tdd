---
name: memory-consolidate
description: "Trigger: promote staged Markdown memory into canonical project memory files."
license: Apache-2.0
---

# Memory Consolidate

Use when staged observations should become durable project memory.

## Protocol

1. Read staged observations, active handoffs, and relevant task sessions.
2. Promote durable items into canonical files such as `current-state.md`, `decisions.md`, `architecture.md`, `risks.md`, and `open-questions.md`.
3. Keep canonical entries short, dated where useful, and linked to source session or handoff files.
4. Update `hot.md` and `index.md` only inside `machine/agent-memory/`.

Do not write to central wiki files such as `machine/hot.md` or `machine/knowledge/index.md`.
