# 2026-10-08 Model Routing Experiment

## Status

The canonical workflow now uses GPT-6 Sol for requirements and design, GPT-6
Luna with high reasoning for approved small-layer test/implementation work, and
Claude Sonnet 5.5 with medium reasoning for standard-layer test/implementation
work. `layer_size` is approved in the layer todo; missing size defaults to
`standard`, and invalid values stop context preparation. Documentation-only
layers qualify as small. The red-gate recorder now keeps its visible table in
sync with frontmatter and the human decision log.

For planning, the two completed routed runs plus an extrapolation of the
stopped third run give an estimated three-run median of **$1.6334**, about
**36% below** the $2.5615 midpoint of the two completed original-model runs.
Treat this as the provisional cost baseline for the next benchmark; no more
model-backed runs are planned now. It measures the pre-repair, Sonnet High
route. It does not measure Sonnet Medium or establish a third completed oracle
pass. The repaired candidate passed a one-layer diagnostic before the effort
change; a fresh diagnostic and matched old-model control were not run.

## Observed Runs

All completed runs used Conductor `0.1.42`, Copilot, `make test`, Graphify off,
the fixed idempotent-cancellation request, and the same gate playbook. The full
oracle and forbidden-path check passed wherever shown below.

| Workflow | Scope | Cost USD | Input tokens | Calls | Oracle / boundary | Use |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Original `gpt-5.4` | L10 diagnostic | 1.0334 | 874,744 | 6 | pass / pass | Diagnostic only |
| Routed, before recorder repair | L10 diagnostic | 0.4942 | 820,908 | 6 | pass / pass | Diagnostic only |
| Original `gpt-5.4`, run 1 | Three layers | 2.6841 | 2,329,522 | 15 | pass / pass | Pre-repair observation |
| Original `gpt-5.4`, run 2 | Three layers | 2.4389 | 2,019,677 | 15 | pass / pass | Pre-repair observation |
| Routed, before recorder repair, run 1 | Three layers | 1.6391 | 2,080,696 | 15 | pass / pass | Pre-repair observation |
| Routed, before recorder repair, run 2 | Three layers | 1.6223 | 2,350,641 | 15 | pass / pass | Pre-repair observation |
| Routed, before recorder repair, run 3 | Five calls measured; remainder estimated | 1.6334 est. | 2,094,394 est. | 5 measured + 10 estimated | stopped at checkpoint / boundary unverified | Provisional cost sample |
| Routed, repaired recorder | L10 diagnostic | 0.4947 | 794,381 | 6 | pass / pass | Diagnostic only |

The stopped run found a genuine artifact contradiction: frontmatter and the
decision log authorized implementation, but the Red-Test Gate table still said
`blocked` and `No`. The Luna implementor requested a human checkpoint. The
recorder repair updates that table atomically with the decision. The repaired
diagnostic showed `observed-red` and `Yes` before implementation, then passed.

The run-3 event log records five completed model calls costing $0.397372 and
667,801 input tokens. The corresponding remaining ten calls cost $1.226336
and $1.245631 in routed runs 1 and 2. Adding their mean to the measured prefix
gives **$1.633356**; the analogous input-token estimate is **2,094,394**.
Using either observed tail yields $1.623708–$1.643003. The provisional routed
median is $1.633356 (runs 1 and 2: $1.639075 and $1.622334). The two-run
original-model midpoint is $2.561472 (runs: $2.684071 and $2.438872), so the
estimated cost reduction is 36.2%.

This imputation is accepted as run 3 for **cost planning and future benchmark
comparison**, with its estimated status retained. The interrupted run did not
complete L10 or the remaining layers, so no quality result is imputed. The
original and routed cohorts used different prompts and model policies; both
precede the recorder repair and Sonnet Medium change.

## Comparison Boundary

The frozen original workflow tree is at
`/tmp/ltdd-model-routing-control/conductor` (SHA-256 tree digest
`a022bbd17fbf1fb1f767e3eb4a0176f531d44cb35ae7883ac8184f4d40b9184e`).
A matched control with the repaired prompts and recorder but the original
`gpt-5.4`/`gpt-5.4-mini` model policy is at
`/tmp/ltdd-model-routing-matched-control/conductor` (digest
`c9c6cd2735df6d4b5367cbc003b9e66fcfe476c1ce543fbe736b12d53188ef94`).
The matched tree shares the repaired prompts and recorder with the current
candidate; its workflow YAML differs only in model and reasoning assignment.
Conductor validation, the Python tests, Go tests, installation comparison, and
diff check pass for the current candidate. The fresh Sonnet Medium diagnostic
was materialized but could not start: outside-sandbox execution was rejected.

For a later benchmark, carry forward the provisional $1.6334 routed cost
median, its measured-plus-estimated provenance, and the two-run $2.5615
original-model midpoint. Compare future results against them as planning
references. A measured Sonnet Medium or quality claim will require live
evidence when benchmarking becomes affordable again.
