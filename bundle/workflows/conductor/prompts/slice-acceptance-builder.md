Build the human-approved slice acceptance mechanism before internal layers.

Approved plan: `{{ slice_acceptance_planner.output.artifact_path }}`

Read the plan and only the named sources plus necessary callers for those exact
files. Change only `allowed_files`. Preserve the plan and every other file.
Build real tests, DSL/helpers, protocol drivers, fixtures and the explicitly
approved persistence/model support. Production changes are allowed only for
those approved setup representations, never feature business logic or fabricated
outcomes. Keep existing dirty work intact. Use existing helpers and dependencies.

Run focused checks if useful. Conductor verifies the complete approved readiness,
regression and each named acceptance test after your work. Implement every
listed test in its protected file and use the approved runner output. Each AT must actually execute
and fail for missing business behavior; compilation failure, unavailable data,
undefined helpers, fake drivers and broken infrastructure are blockers. If the
approved design cannot achieve that, stop editing and report the concrete issue;
verification will block and the human must revise the plan before broader edits.
Do not edit the approval state or introduce another runnable workflow.

Return `summary` with actual changes and unresolved blockers.

{% for star in [star_step] if star is defined and star.output is defined %}
## Star workflow

{% if star.output.objective is defined and star.output.objective %}Objective: {{ star.output.objective }}
{% endif %}{% if star.output.brief is defined and star.output.brief %}Brief from the hub for this visit (prioritize it over your default next step): {{ star.output.brief }}
{% endif %}The star router chooses what runs next; do not assume a fixed next step. Also return the star return contract: `outcome` (`done`, `blocked`, `needs-human` or `incoherence`), `reason`, `artifacts` (repository-relative paths you created or changed) and `incoherence` as `{"claim": ..., "source": ..., "conflicts_with": ...}` when an approved contract, test or artifact contradicts the work, otherwise `{}`. Report an incoherence instead of editing a protected contract.
{% endfor %}
