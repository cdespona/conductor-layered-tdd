---
name: memory-capture
description: "Trigger: stage concise candidate Markdown memories during active project work."
license: Apache-2.0
---

# Memory Capture

Use when active work creates a fact, decision, risk, open question, or handoff note worth preserving.

## Protocol

- Write only under `machine/agent-memory/projects/<project>/`.
- Prefer task session files, `handoffs/<task-slug>.md`, and `inbox/staged-observations.md`.
- Keep entries concise and structured.
- Do not persist raw command output, stack dumps, full transcripts, full chat logs, secrets, tokens, credentials, private keys, cookies, auth headers, or full sensitive config dumps.
- Do not update canonical memory files; use `memory-consolidate` for that.
