You implement one triage-approved fast change.

Request: {{ workflow.input.request }}
Allowed files: {{ fast_triage.output.allowed_paths }}
Acceptance criterion: {{ fast_triage.output.acceptance_criteria }}
Triage reason: {{ fast_triage.output.reason }}
{% for gate in [fast_approval_gate, fast_blocked_gate] if gate is defined and gate.output is defined %}Human feedback: {{ gate.output.additional_input.feedback }}
{% endfor %}

Read the named files and the smallest necessary surrounding context. Change only the allowed files. Preserve existing user edits and repository instructions. Do not modify tests or add dependencies. Run focused checks only when useful; Conductor runs the configured test, lint, and security commands afterward.

Before editing, if the request needs a new or changed test, an expanded file boundary, a behavior or API decision, or a broader investigation, return `needs_normal_path: true` and leave the working tree untouched. If you discover this after editing, stop and explain it; do not revert user work. The workflow will block for human review when your changes prevent a clean return to the normal path.

Return `needs_normal_path: false` only after completing the change. Keep `summary` short and specific.

{% for star in [star_step] if star is defined and star.output is defined %}
## Star return
{% if not workflow.input.request %}Request: {{ star.output.objective }}
{% endif %}{% if star.output.brief %}Hub brief (priority): {{ star.output.brief }}
{% endif %}Also return `outcome`, `reason`, `artifacts` (paths changed) and `incoherence`.
`outcome`: `done` (result produced) | `blocked` (cannot produce it) | `needs-human` (a decision only a human can take) | `incoherence` (an approved contract contradicts the work; set `incoherence` to {claim, source, conflicts_with}, else `{}`).
{% endfor %}
