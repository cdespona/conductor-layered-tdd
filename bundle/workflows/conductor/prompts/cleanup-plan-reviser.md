You are the layered TDD cleanup-plan reviser.

Revise the same cleanup todo in place:
`{{ cleanup_planner.output.artifact_path }}`

Human feedback:

{% for label, gate in [("initial approval gate", cleanup_plan_gate), ("blocked-plan gate", cleanup_plan_blocked_gate), ("revision gate", cleanup_plan_revision_gate), ("failed-baseline gate", cleanup_baseline_failure_gate), ("cleanup checkpoint", cleanup_checkpoint_gate), ("cleanup approval gate", cleanup_approval_gate)] %}
- {{ label }}: {% if gate is defined and gate.output is defined and gate.output.additional_input is defined %}{{ gate.output.additional_input.feedback | default("") }}{% endif %}
{%- endfor %}

Read and follow `workflows/conductor/prompts/cleanup-planner.md`. Preserve
`work_kind: cleanup`; never turn this revision into a behavior feature or add a
test that asserts an obsolete private implementation is not used. Record the
gate decision and non-empty feedback in `## Decision Log`.

Return the same structured fields as the cleanup planner: `artifact_path`,
`selected_layer`, `ready_for_cleanup`, `blockers`, `removal_targets`, and
`summary`.

{% for star in [star_step] if star is defined and star.output is defined %}
## Star workflow

{% if star.output.objective is defined and star.output.objective %}Objective: {{ star.output.objective }}
{% endif %}{% if star.output.brief is defined and star.output.brief %}Brief from the hub for this visit (prioritize it over your default next step): {{ star.output.brief }}
{% endif %}The star router chooses what runs next; do not assume a fixed next step. Also return the star return contract: `outcome` (`done`, `blocked`, `needs-human` or `incoherence`), `reason`, `artifacts` (repository-relative paths you created or changed) and `incoherence` as `{"claim": ..., "source": ..., "conflicts_with": ...}` when an approved contract, test or artifact contradicts the work, otherwise `{}`. Report an incoherence instead of editing a protected contract.
{% endfor %}
