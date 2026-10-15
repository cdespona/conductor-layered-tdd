You route a code-changing request after preflight and the Graphify choice. Inspect only enough of the repository to make a confident decision. Do not edit files or run project commands.

Request: {{ workflow.input.request }}
{% if workflow.input.resume_path %}Existing plan: {{ workflow.input.resume_path }}{% endif %}

{% if graphify_refresh is defined and graphify_refresh.output is defined and graphify_refresh.output.graph_available %}
A refreshed Graphify graph is available. Use bounded Graphify queries to locate the files and dependencies relevant to the request, then verify returned source locations before deciding the route and allowed paths.
{% elif graphify_status is defined and graphify_status.output is defined and graphify_status.output.graph_available and graphify_update_gate is defined and graphify_update_gate.output is defined and graphify_update_gate.output.selected == 'use_existing' %}
An existing Graphify graph is available. Use bounded Graphify queries to locate the files and dependencies relevant to the request, then verify returned source locations before deciding the route and allowed paths.
{% endif %}

Return `route: fast` only when all of these hold:

- The request is a documentation correction, a small visual adjustment, or a localized refactor with no intended behavior change.
- No test needs to be added or changed, and no test-first investigation is needed. Existing tests may still be run after implementation.
- Acceptance criteria are clear, and you can name every file the implementor may edit. Use exact repository-relative file paths, not directories or globs, and at most three files.
- No API, data, event, security, migration, cross-component, or ambiguous design decision is involved.
- The change does not edit agent instructions such as `AGENTS.md` or `.github/copilot-instructions.md`.
- No existing plan is being resumed.

If any condition is uncertain, return `route: standard` with an empty `allowed_paths` array and an empty `acceptance_criteria` string. A file being short is not enough to qualify. Do not change the request or generate a plan artifact. State the concrete reason and, for a fast route, one concise acceptance criterion.

Return the structured output as **values**, not as a schema. In particular, `route` must be the string `fast` or `standard`, never an object containing `type` or `description`.

For a request that needs the full workflow, the output values should have this shape:

```json
{"route":"standard","reason":"This changes observable behavior and needs tests.","allowed_paths":[],"acceptance_criteria":""}
```
