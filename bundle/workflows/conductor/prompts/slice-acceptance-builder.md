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
