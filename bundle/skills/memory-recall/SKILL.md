---
name: memory-recall
description: "Trigger: recall Markdown memory for the active project without scanning the whole vault."
license: Apache-2.0
---

# Memory Recall

Use when you need prior project context from the Markdown memory backend.

## Protocol

1. Read `machine/agent-memory/hot.md`.
2. Read `machine/agent-memory/projects/<project>/index.md`.
3. Read targeted project files such as `current-state.md`, `decisions.md`, `risks.md`, `open-questions.md`, or `handoffs/<task-slug>.md`.
4. Search session files by task slug only when needed.
5. Query curated wiki knowledge only when explicitly relevant, using read-only access to `machine/knowledge/index.md`, `machine/hot.md`, and targeted `machine/knowledge/*.md` files selected from the index.

Do not read the entire vault. Do not read `human/`, `sources/`, or `_raw/` unless the user explicitly asks and the task requires it.
