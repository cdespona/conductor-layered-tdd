# 2026-09-16 Pre-Test-Author Versus Test-Author Benchmark Ledger

## Compatibility Contract

| Field | Frozen control | Confirmed candidate | Result |
| --- | --- | --- | --- |
| Conductor | `0.1.34` | `0.1.34` | Match |
| Fixture and request | `idempotent-cancellation` | `idempotent-cancellation` | Match |
| Layers and verification | `L10-domain` -> `L20-service` -> `L30-http`; `make test-noisy` | Same | Match |
| Runtime | Copilot `gpt-5.4`; documented per-agent reasoning efforts | Same | Match |
| Graphify | Disabled | Disabled | Match |
| Human decisions | Fixed approval/skip playbook | Same decisions | Match |
| Retries | `0` in every run | `0` in every run | Match |

The test-author handoff route intentionally differs: the frozen control routes
directly to `test_author_context`, while the candidate routes through
`test_author_preparer` and its deterministic preparation/recording steps. That
is the evaluated feature, not an uncontrolled route mismatch.

## Registered Runs

| Cohort | Runs | Status | Source artifacts |
| --- | ---: | --- | --- |
| Pre-test-author control `pre-test-author-3ac059b-noisy` | 3 | Complete comparable baseline; oracle and boundary pass | `/tmp/ltdd-benchmark-20260916.TFKO9B/results/control-01.json` through `control-03.json` |
| First test-author attempt `current-c9ba7c3-noisy` | 2 passes + 1 stopped attempt | Diagnostic history only | `/tmp/ltdd-benchmark-20260916.TFKO9B/results/candidate-01.json`, `candidate-02.json`, and `candidate-03.txt` |
| Completed test-author candidate with boundary-contract repairs | 3 | Complete comparable candidate; oracle and boundary pass | `/tmp/boundary-schema-candidate-1.result.json` through `candidate-3.result.json` |

The stopped run belongs to the **first test-author candidate attempt**, not to
the pre-test-author cohort. Its L20 todo put the same test path in editable and
read-only sets, so the fail-closed test-author checkpoint rejected it. The
subsequent boundary-contract and source-aware parser repairs are correctness
repairs to the test-author feature. The final three-run cohort measures that
completed feature against the three complete pre-test-author runs.

## Per-Run Results

| Cohort | Run | Input tokens | Output tokens | Total tokens | Cost estimate | Invocations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pre-test-author | 1 | 2,385,559 | 87,169 | 2,472,728 | $2.9848 | 15 |
| Pre-test-author | 2 | 2,467,371 | 80,441 | 2,547,812 | $2.7636 | 15 |
| Pre-test-author | 3 | 2,505,946 | 83,163 | 2,589,109 | $2.8755 | 15 |
| Completed test-author | 1 | 2,399,219 | 82,446 | 2,481,665 | $2.8491 | 15 |
| Completed test-author | 2 | 2,235,487 | 88,691 | 2,324,178 | $2.7880 | 15 |
| Completed test-author | 3 | 2,094,417 | 81,680 | 2,176,097 | $2.6136 | 15 |

## Median Comparison

| Metric | Pre-test-author median (3) | Completed test-author median (3) | Saving |
| --- | ---: | ---: | ---: |
| Input tokens | 2,467,371 | 2,235,487 | 231,884 (9.40%) |
| Output tokens | 83,163 | 82,446 | 717 (0.86%) |
| Total tokens | 2,547,812 | 2,324,178 | 223,634 (8.78%) |
| Telemetry cost estimate | $2.8755 | $2.7880 | $0.0875 (3.04%) |
| Input tokens per run layer | 822,457 | 745,162 | 77,295 (9.40%) |
| Cost estimate per run layer | $0.9585 | $0.9293 | $0.0292 (3.04%) |
| Prompt bytes | 204,817 | 197,566 | 7,251 (3.54%) |
| Tool-output bytes | 70,683 | 63,014 | 7,669 (10.85%) |
| Script-output bytes | 217,349 | 206,657 | 10,692 (4.92%) |
| Model invocations | 15 | 15 | No change |

Every frozen-control and confirmed-candidate run passed the full oracle and
boundary check, completed all three layers, and had zero retries.

## Test-Author Stage Comparison

The feature's targeted stage shows a substantially larger improvement than the
end-to-end total. Unrelated stochastic variation in mapping, todo generation,
and requirements work offsets part of the test-author saving at whole-run
level.

| Test-author metric | Pre-test-author median | Completed test-author median | Saving |
| --- | ---: | ---: | ---: |
| Input tokens | 744,510 | 280,817 | 463,693 (62.28%) |
| Output tokens | 25,030 | 11,032 | 13,998 (55.92%) |
| Total tokens | 769,540 | 291,849 | 477,691 (62.07%) |
| Telemetry cost estimate | $0.8000 | $0.3722 | $0.4278 (53.48%) |
| Tool calls | 44 | 16 | 28 (63.64%) |
| Prompt bytes | 45,822 | 36,180 | 9,642 (21.04%) |
| Tool-output bytes | 14,530 | 2,837 | 11,693 (80.47%) |
| Model invocations | 3 | 3 | No change |

These medians are computed independently per metric. Consequently, stage
savings must not be algebraically added to whole-run savings.

## First Test-Author Attempt

The two successful `current-c9ba7c3-noisy` test-author runs had a median of
1,976,329 input tokens and $2.5089 estimated cost. They suggest a larger
earlier reduction, but do not support a benchmark claim because the third run
stopped at an unexpected human checkpoint. Do not add this observation to the
confirmed savings above or sum it with later results.

## Earlier Combined-Repository Observation

The older combined `gentle-ai-fork` history preserves a comparison of
1,444,965 versus 1,051,123 input tokens, a reduction of 393,842 (27.26%). It
also records 34 versus 24 model invocations and four versus three
implementation/review cycles. The original result artifacts were not found in
this checkout, and the route shapes differ, so this is retained as historical
context only—not as an additive or controlled saving.

## Claim

The confirmed, non-additive claim is: the completed test-author feature reduces
its own stage's median input tokens by 62.28% and cost estimate by 53.48%. At
whole-run level it reduces median input tokens by 9.40% and median cost estimate
by 3.04% against the compatible three-run pre-test-author baseline, while
preserving full-oracle and boundary success.
