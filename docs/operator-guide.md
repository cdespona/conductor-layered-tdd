# Conductor Layered TDD Operator Guide

This project installs the runnable Microsoft Conductor layered-TDD workflow. Use this guide when you are returning to a half-finished run and need to know which artifact to open, which gate to choose, and which frontmatter values are meaningful.

## Mental Model

```mermaid
flowchart LR
    Request["User request"] --> Preflight["Preflight checks"]
    Preflight --> Graphify{"Refresh Graphify?"}
    Graphify --> Requirements["00-requirements.md"]
    Requirements --> Slice{"Multiple slices?"}
    Slice -- "yes" --> SliceDoc["slice-selection.md"]
    SliceDoc --> SliceRun["fresh slice run"]
    Slice -- "no" --> LayerMap["01-layer-map.md"]
    SliceRun --> LayerMap
    LayerMap --> LayerTodo["layers/<nn>-<layer>.todo.md"]
    LayerTodo --> Kind{"work_kind"}
    Kind -- "behavior or missing" --> Context["bounded behavior context"]
    Kind -- "cleanup" --> CleanupPlan["cleanup plan approval"]
    CleanupPlan --> CleanupBase["green baseline"]
    CleanupBase --> Cleanup["remove superseded implementation"]
    Cleanup --> CleanupCheck["removal + reference + green checks"]
    CleanupCheck --> Review
    Context --> RedGate["Run full test suite"]
    RedGate --> RedDecision{"Review red/green evidence"}
    RedDecision -- "revise" --> LayerTodo
    RedDecision -- "approved" --> Implement["Implement layer"]
    Implement --> Verify["test + lint + audit"]
    Verify --> Review["Layer review notes"]
    Review --> More{"More layers?"}
    More -- "yes" --> LayerMap
    More -- "no" --> Final["99-final-review.md"]
    Final --> Memory{"Capture memory?"}
    Memory -- "yes" --> Capture["Markdown memory capture"]
    Memory -- "no" --> Done["Done"]
    Capture --> Done
```

| Rule | Meaning |
| --- | --- |
| One run should finish one selected slice. | If the request is large, split it first, then run one slice. |
| Humans own top-level behavior. | You approve requirements, layer choice, Gherkin, red-test state, checkpoints, and final memory. |
| Agents own bounded implementation. | The implementor may work only inside the approved layer boundary. |
| Cleanup is an explicit layer kind. | It may occur anywhere dependencies allow; it skips test authoring and expected-red, but adds green-before/green-after and removal/reference checks. |
| Artifacts are the memory of the run. | They contain the contract, evidence, and persistent decision log. Edit them only to change that meaning. |
| Conductor decisions are authoritative. | Choose an option and add an optional comment in its gate. The flow synchronizes the resulting state into the artifact. |
| Frontmatter is recovery context. | It is updated by workflow agents; do not maintain duplicated dashboard state by hand. It does not yet route a new run past completed phases. |

## Files In This Directory

| Path | Role |
| --- | --- |
| `layered-tdd.yaml` | Runnable Conductor workflow. |
| `prompts/requirements-griller.md` | Writes `slice-selection.md` or `00-requirements.md`. |
| `prompts/slice-run-starter.md` | Creates a slice-specific `00-requirements.md` after slice selection. |
| `prompts/layer-mapper.md` | Writes `01-layer-map.md` and skeleton layer todos. |
| `prompts/layer-todo-generator.md` | Details the selected layer todo, Gherkin, test ownership, and red-test gate. |
| `prompts/cleanup-planner.md` | Details a selected cleanup layer and proves its supersession/preservation contract. |
| `prompts/cleanup-implementor.md` | Removes only the approved private supersession chain. |
| `prompts/cleanup-reviewer.md` | Reviews cleanup evidence without inventing implementation-coupled absence tests. |
| `scripts/build-layer-context.py` | Derives bounded, hashed source projections for todo generation, test authoring, and implementation. |
| `scripts/record-test-author.py` | Records test-author approval/task state and validates that the model changed only reported in-boundary tests. |
| `scripts/route-selected-layer.py` | Defaults missing `work_kind` to behavior and routes explicit cleanup layers separately. |
| `scripts/check-cleanup-result.py` | Checks declared removal paths and bounded fixed-string references after cleanup. |
| `scripts/run-verification.py` | Runs a verification command, stores complete output, and returns bounded evidence. |
| `scripts/record-red-gate.py` | Deterministically records red-suite evidence and the human red/green decision. |
| `scripts/capture-layer-snapshot.py` | Captures the exact pre-implementation Git tree, including uncommitted files. |
| `scripts/create-layer-diff.py` | Produces a bounded layer-only patch and deterministic boundary classification. |
| `prompts/implementor.md` | Implements one approved layer and records checkpoints. |
| `prompts/layer-reviewer.md` | Reviews one implemented layer after verification commands run. |
| `prompts/final-reviewer.md` | Writes `99-final-review.md`. |
| `prompts/memory-capturer.md` | Captures human-approved final memory candidates. |

## Start Or Resume

### Start

```bash
conductor run workflows/conductor/layered-tdd.yaml \
  --workspace-instructions \
  --web \
  --input request="Describe the code-changing task"
```

### Start With Explicit Commands

```bash
conductor run workflows/conductor/layered-tdd.yaml \
  --workspace-instructions \
  --web \
  --input request="Describe the code-changing task" \
  --input test_command="make test" \
  --input lint_command="make lint" \
  --input security_command="make audit"
```

### Resume A Failed Workflow Checkpoint

```bash
conductor resume workflows/conductor/layered-tdd.yaml --web
```

Use this when Conductor saved a checkpoint after a workflow failure. It restores
the workflow state and prior agent outputs, then continues from the failed
agent. It does not inspect artifact frontmatter to select a different phase.

### Reopen A Slice Folder In A New Run

```bash
conductor run workflows/conductor/layered-tdd.yaml \
  --workspace-instructions \
  --web \
  --input request="Resume the active layered TDD slice" \
  --input resume_path=".github/plans/<slice-slug>"
```

`resume_path` is authoritative: the griller revises that folder's existing
`00-requirements.md` and must not create a sibling plan folder. This is **not**
`conductor resume`: it starts a new run at `preflight_tests`, then reaches the
requirements griller. Frontmatter is useful context for that agent, but does
not currently make Conductor skip already-completed phases.

### Start With Markdown Memory

```bash
conductor run workflows/conductor/layered-tdd.yaml \
  --workspace-instructions \
  --web \
  --input request="Describe the code-changing task" \
  --input memory_vault="/absolute/path/to/vault" \
  --input memory_namespace="machine/agent-memory" \
  --input memory_project="<project-slug>"
```

## Workflow Inputs

| Input | Default | Required | Used by | Notes |
| --- | --- | --- | --- | --- |
| `request` | none | yes | Requirements griller | The code-changing task. |
| `task_slug` | generated by agent | no | Requirements griller | Optional folder name under `.github/plans/`. |
| `test_command` | `make test` | no | Preflight, red gate, and verification | Must be green before analysis and after implementation; after a newly added top-level test, its result is recorded as red/green evidence for human review. |
| `lint_command` | `make lint` | no | Preflight and verification | Static checks. |
| `security_command` | `make audit` | no | Preflight and verification | Dependency/CVE/audit checks. |
| `graphify_scope` | none | no | Graphify refresh gate | Focused code directory for a first build or an explicit refresh. Leave empty to refresh an existing graph from its saved root. |
| `resume_path` | none | no | Requirements griller | Existing `.github/plans/<slug>` folder for a new run; pins requirements to that path but does not skip preflight or requirements. |
| `memory_vault` | none | no | Requirements and final capture | Absolute Markdown memory vault root. |
| `memory_namespace` | `machine/agent-memory` | no | Requirements and final capture | Namespace under the memory vault. |
| `memory_project` | none | no | Requirements and final capture | Project slug under the memory namespace. |

## Graphify And Explicit Context

This workflow uses `context.mode: explicit`. Each model-backed stage receives
only its declared artifact paths, gate decisions, and small structured outputs.
The layer agents receive deterministic, byte-bounded projections of files named
by the active boundary. The layer reviewer receives bounded verification
summaries and a bounded layer-only patch; complete evidence remains available by
path. Human gates remain the review surface; the map and todo remain the durable
authoritative handoff.

```mermaid
flowchart LR
    Request["original user contract"] --> Project["build-layer-context.py"]
    Map["selected map + todo"] --> Project
    Boundary["allowed / forbidden / read-only paths"] --> Project
    Source["current named source files"] --> Project
    Project --> Todo["todo generator: 12 KB source cap"]
    Project --> Test["test author: 16 KB source cap"]
    Project --> Impl["implementor: 20 KB source cap"]
```

Each projection records source hashes and is regenerated immediately before its
consumer. It is navigation evidence, not a second editable contract. Agents may
open a named projected file when an excerpt is insufficient, but must not use a
broad repository scan as their default discovery strategy. The bounded original
request is carried alongside derived artifacts so exact signatures and return
shapes cannot be weakened by successive summaries.

```mermaid
flowchart LR
    Security["Preflight security"] --> Status["Detect graph"]
    Status --> Gate{"Refresh Graphify?"}
    Gate -->|"focused scope"| Refresh["Build or update graph"]
    Gate -->|"existing or skip"| Requirements["Requirements"]
    Refresh --> Requirements
```

At the Graphify gate, use a focused code scope for the first build—such as
`src`, `internal`, or `packages/api`—rather than a large mixed repository root.
Pass it at start:

```bash
conductor run workflows/conductor/layered-tdd.yaml \
  --workspace-instructions \
  --web \
  --input request="Describe the code-changing task" \
  --input graphify_scope="src"
```

Graphify is optional. When available, requirements discovery, layer mapping,
todo design, test authoring, implementation, and review issue bounded queries,
verify returned source locations, and never rebuild the graph themselves.

## Artifact Map

```mermaid
flowchart TB
    Root[".github/plans/"] --> Discovery["<discovery-slug>/"]
    Discovery --> SliceSelection["slice-selection.md"]

    Root --> Slice["<slice-slug>/"]
    Slice --> Req["00-requirements.md"]
    Slice --> Map["01-layer-map.md"]
    Slice --> Layers["layers/"]
    Layers --> Todo["<nn>-<layer>.todo.md"]
    Slice --> Final["99-final-review.md"]
```

| Artifact | Created when | Human reads it to decide | Primary gate |
| --- | --- | --- | --- |
| `slice-selection.md` | Request has multiple independently valuable slices. | Which one slice to run now. | Slice selection. |
| `00-requirements.md` | One slice is ready for confirmation. | Goal, blockers, assumptions, and out-of-scope work. | Requirements. |
| `01-layer-map.md` | Requirements are approved. | Which layer to detail next. | Layer selection. |
| `layers/<nn>-<layer>.todo.md` | A layer exists in the map. | Gherkin, test ownership, red-test state, implementation boundary, tasks, review notes. | Layer todo, checkpoint, layer approval. |
| `99-final-review.md` | Final layer is approved. | Residual risks and memory candidates. | Memory. |

## Artifact Style Contract

Generated artifacts are intentionally visual-first. When you return to a run after days away, the top of each artifact should tell you where you are and what to do next without rereading paragraphs.

| Artifact | Must lead with | Main visual tools |
| --- | --- | --- |
| `slice-selection.md` | Slice decision dashboard. | Slice options table, Mermaid fresh-run flow, human decision field. |
| `00-requirements.md` | Status dashboard. | Scope map, blockers table, assumptions table, memory-used table. |
| `01-layer-map.md` | Status dashboard and layer flow. | Mermaid layer flow, layer matrix, selection board, risk table. |
| `layers/<nn>-<layer>.todo.md` | Gate dashboard. | Red-test gate table, implementation boundary table, task board, risk board. |
| Layer checkpoint section | Checkpoint dashboard. | Mismatch/scope-change table and route-options table. |
| Layer review section | Review dashboard. | Verification matrix, boundary check, issues/risks table. |
| `99-final-review.md` | Completion dashboard. | Slice flow, layer completion matrix, verification matrix, residual risks, memory candidates. |
| Memory capture section | Capture dashboard. | Capture matrix with captured/staged/promoted/skipped outcome. |

Prose should explain only what tables cannot: rationale, caveats, exact evidence, or final judgment. If an artifact becomes a long narrative, revise it into dashboards and tables before relying on it as workflow state.

## Gate Cheat Sheet

| Gate | Open this first | Choose this when ready | Choose revise when... | Choose stop when... |
| --- | --- | --- | --- | --- |
| Preflight failure | Command output in dashboard | Retry after fixing, or continue with waiver. | Not applicable. | Existing checks should be fixed outside this run. |
| Slice selection | `slice-selection.md` | Exactly one slice is selected. | The split is wrong or too large. | You only wanted discovery. |
| Requirements | `00-requirements.md` | One slice goal is clear and blockers are resolved or accepted. | You edited requirements or answered blockers in the file. | The slice is not worth doing. |
| Layer selection | `01-layer-map.md` | One next layer is selected. | Boundaries, order, or skeleton todos are wrong. | You do not want to continue this slice. |
| Layer todo | `layers/<nn>-<layer>.todo.md` | Gherkin, test ownership, and red-test gate are acceptable. | The contract, test mode, or red-test state is wrong. | The layer should not proceed. |
| Cleanup plan | Cleanup todo with `work_kind: cleanup` | Replacement is active, consumers migrated, preservation tests exist, and exact removal/reference checks are bounded. | Any consumer, observable change, target, or safety evidence is unclear. | Cleanup should not proceed. |
| Cleanup baseline | Bounded test/lint/security evidence | All three checks are green immediately before removal. | Revise if the cleanup contract caused the mismatch. | Existing failures cannot be resolved safely. |
| Checkpoint | Active layer todo | The checkpoint is not actually new top-level behavior, or you chose a route. | Route to layer todo or layer selection if scope changed. | The contradiction blocks the slice. |
| Layer approval | Active layer todo | Approve and choose another layer, or approve and final review. | Send back for focused fixes. | You want to pause after review. |
| Memory | `99-final-review.md` | Capture approved candidates, or finish without capture. | The final review or memory candidates are wrong. | Not offered; use skip to finish. |

### Revision guarantee

Choosing **revise** at a requirements, slice-selection, layer-map, or layer-todo
gate keeps the workflow on the current artifact. The workflow routes to a
dedicated reviser with the creator's original artifact path, so it updates that
file in place and must not infer a new slug or create a sibling plan folder.

```mermaid
flowchart LR
    Create["Create active artifact"] --> Review["Human review"]
    Review -->|"revise"| Reviser["Pinned reviser"]
    Reviser -->|"same path"| Review
    Review -->|"approve"| Next["Next workflow stage"]
```

## Frontmatter Rules

### Layer work kind

`work_kind` controls routing, not naming or ordering:

| Value | Route | Test rule |
| --- | --- | --- |
| `behavior` or missing | Existing Gherkin → test author/existing test → expected-red → implementor → reviewer path. | Tests describe observable behavior. |
| `cleanup` | Cleanup planner → green baseline → cleanup implementor → removal/reference checks → cleanup reviewer. | Do not add a permanent test that merely proves an old private implementation is absent or unused. Existing behavior tests stay; named implementation-coupled tests may be removed with obsolete code. |

```mermaid
flowchart LR
    Select["Human selects any layer id"] --> Route{"work_kind"}
    Route -- "behavior/default" --> TDD["normal layered TDD"]
    Route -- "cleanup" --> Ready{"replacement active + consumers migrated + behavior covered?"}
    Ready -- "no" --> Harden["separate behavior/test-hardening layer"]
    Ready -- "yes" --> Green1["green baseline"]
    Green1 --> Remove["bounded removal"]
    Remove --> Exact["targets absent + bounded references absent"]
    Exact --> Green2["green post-checks + review"]
```

Ordinary cleanup is limited to a private supersession chain. Public API,
persisted data/schema, external events, compatibility windows, or cross-release
retirement must use a behavior/migration layer. Mixed or unknown work fails
closed to the behavior route.

Every artifact should start with YAML frontmatter. Keep it boring, explicit, and small.

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-confirmation
---
```

| Field | Applies to | Values | Meaning |
| --- | --- | --- | --- |
| `workflow` | all artifacts | `layered-tdd` | Identifies this artifact as part of this workflow. |
| `owner` | all artifacts | `human`, `agent`, specific agent name | Who currently needs to act. Updated by the workflow. |
| `status` | all artifacts | artifact-specific | Durable state for humans and agents. Updated by the workflow. |
| `selected_slice` | `slice-selection.md` | slice slug or id | Selected in the Conductor gate and persisted automatically. |
| `selected_layer` | `01-layer-map.md`, `layers/*.todo.md` | layer id or todo filename | The gate routes through a deterministic recorder that updates `01-layer-map.md` before todo generation. |
| `test_ownership` | `layers/*.todo.md` | `human-written`, `agent-written-after-approval`, `waived` | Who owns top-level test creation for this layer. |
| `red_gate_state` | `layers/*.todo.md` | `observed-red`, `not-run-human-approved`, `already-passing-human-approved`, `waived`, `blocked` | Whether production implementation may start. |
| `memory_decision` | `99-final-review.md` | `capture`, `skip`, `revise` | Human memory intent, persisted from the gate. |

### Important Routing Reality

```mermaid
flowchart LR
    Gate["Choose option + optional comment in Conductor"] --> Sync["Workflow synchronizes artifact"]
    Sync --> Agent["Next agent reads snapshot, contract, and decision log"]
    Agent --> Route["Workflow route continues"]
```

Conductor routes through the human gate option you choose in the web UI. Use the optional gate comment for an approval reason, a caveat, or concise feedback. The matching workflow step persists it in the artifact's `## Decision Log`; use a Markdown edit only when you are changing the actual contract, Gherkin, scope, or evidence.

## Artifact Frontmatter Catalog

### `slice-selection.md`

Created only when the request contains multiple valuable slices.

```yaml
---
workflow: layered-tdd
owner: human
status: slice-selection-required
selected_slice:
---
```

| Field | Values | Use this when... | Next dashboard choice |
| --- | --- | --- | --- |
| `status` | `slice-selection-required` | The agent has proposed multiple slices and needs you to choose one. | Select one slice, then choose `I selected one slice and want a fresh slice-specific run`. |
| `status` | `selected` | You wrote the chosen slice into `selected_slice` or clearly marked it in the body. | Choose selected. |
| `status` | `blocked` | None of the slices can proceed without an answer. | Choose revise after adding answers, or stop. |
| `status` | `stopped` | You intentionally ended discovery. | Choose stop. |
| `selected_slice` | slice id, title, or slug | You want the slice starter to create a dedicated `.github/plans/<slice-slug>/00-requirements.md`. | Choose selected. |

Visual state:

```mermaid
stateDiagram-v2
    [*] --> slice_selection_required
    slice_selection_required --> selected: choose one slice
    slice_selection_required --> slice_selection_required: revise split
    slice_selection_required --> stopped: stop
    selected --> SliceSpecificRequirements
```

### `00-requirements.md`

The contract for one selected slice.

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-confirmation
---
```

| Field | Values | Use this when... | Next dashboard choice |
| --- | --- | --- | --- |
| `status` | `needs-human-confirmation` | Requirements are drafted and waiting for your review. | Approve, revise, or stop. |
| `status` | `blocked` | Blocker questions remain unanswered. | Answer in the file, then choose revise. |
| `status` | `confirmed` | You have confirmed goal, blockers, assumptions, and out-of-scope work. | Choose requirements approved. |
| `status` | `revising` | You edited the artifact and want the griller to reconcile it. | Choose revise. |
| `owner` | `human` | You need to answer blockers or approve. | Pick a requirements gate option. |
| `owner` | `requirements-griller` | You want the griller to reread comments and revise. | Choose revise. |

Review checklist:

| Check | Yes means... |
| --- | --- |
| Slice goal is one behavior change. | The run will not sprawl. |
| Blockers are empty or intentionally accepted. | The layer mapper can work without guessing. |
| Assumptions are non-blocking. | Wrong assumptions are risks, not hidden requirements. |
| Out-of-scope work is explicit. | The implementor has a boundary. |
| Memory notes are current or marked stale. | Old memory will not silently override repo facts. |

Visual state:

```mermaid
stateDiagram-v2
    [*] --> needs_human_confirmation
    needs_human_confirmation --> confirmed: approve gate
    needs_human_confirmation --> revising: edit/comment + choose revise
    revising --> needs_human_confirmation: griller rewrites
    needs_human_confirmation --> blocked: blockers remain
    blocked --> revising: answer blockers
    confirmed --> LayerMap
```

### `01-layer-map.md`

The map of real repository boundaries for the selected slice.

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-layer-selection
selected_layer:
---
```

| Field | Values | Use this when... | Next dashboard choice |
| --- | --- | --- | --- |
| `status` | `needs-human-layer-selection` | Layers exist and you need to choose the next one. | Choose selected after marking `selected_layer`. |
| `status` | `confirmed` | Layer map is acceptable and the selected layer is clear. | Choose selected. |
| `status` | `revising` | Boundaries, order, or todo list need changes. | Choose revise map. |
| `status` | `blocked` | The architecture is unclear enough that mapping should stop. | Add clarification, then revise, or stop. |
| `selected_layer` | layer id or todo filename | You want this layer detailed next. | Choose selected. |
| `owner` | `human` | Waiting for layer choice. | Select a layer. |
| `owner` | `layer-mapper` | You want the mapper to reconcile edits. | Choose revise map. |

Layer map must keep this invariant:

```mermaid
flowchart LR
    Map["Active layer list in 01-layer-map.md"]
    Todos["Non-superseded files in layers/"]
    Map <-->|"must match exactly"| Todos
```

Obsolete todo handling:

| Situation | Expected state |
| --- | --- |
| Skeleton todo has no implementation or review history. | Mapper may delete it. |
| Todo has implementation or review history but no longer belongs to map. | Keep file with `status: superseded` and a note pointing to the current map. |

### `layers/<nn>-<layer>.todo.md`

The executable contract for one layer.

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-test-gate
selected_layer: <layer-id>
test_ownership: human-written
red_gate_state: blocked
---
```

#### Status Values

| `status` | Use this when... | Implementation allowed? | Next dashboard choice |
| --- | --- | --- | --- |
| `skeleton` | Mapper created a placeholder but the layer has not been detailed. | No. | Select this layer, then generate todo. |
| `needs-human-test-gate` | Gherkin/test ownership need human approval or full-suite evidence needs review. | No. | Approve Gherkin/test ownership or revise layer todo. |
| `ready-for-implementation` | A human approved the recorded full-suite red/green result. | Yes. | Workflow enters implementation automatically. |
| `in-progress` | Implementor has started this layer. | Already in progress. | Wait for verification/review, or stop if needed. |
| `checkpoint` | Implementor found new top-level behavior, contradiction, or scope expansion. | No further production work until routed. | Use checkpoint gate. |
| `reviewed` | Reviewer appended notes after verification. | No new work unless fixes requested. | Approve layer, request fixes, or final review. |
| `approved` | Human approved this layer. | This layer is done. | Choose another layer or final review. |
| `superseded` | Layer is no longer in the current layer map but has history. | No. | Do not select unless you intentionally restore it in the map. |
| `blocked` | The layer cannot proceed. | No. | Revise todo, select another layer, or stop. |

#### Test Ownership Values

| `test_ownership` | Visual | Meaning | Required human action |
| --- | --- | --- | --- |
| `human-written` | Human -> test -> agent implements | You write or identify the top-level red test. | Add the test or test path/evidence, then approve red gate. |
| `agent-written-after-approval` | Human approves Gherkin -> agent writes test -> human confirms red | Agent may write top-level tests only after you approve Gherkin. | Approve Gherkin first; confirm before production code. |
| `waived` | Human waiver -> agent implements | No top-level red test for this layer. | Record a human-approved reason. |

#### Red-Test Gate Values

| `red_gate_state` | Implementation allowed? | Meaning | Evidence to record |
| --- | --- | --- | --- |
| `observed-red` | Yes | The top-level test failed before production code. | Command, test path, failure summary. |
| `not-run-human-approved` | Yes | You approved proceeding without running the red test. | Reason and scope of approval. |
| `already-passing-human-approved` | Yes | Behavior is already covered/passing and you approved using that as the gate. | Existing test path and why it is enough. |
| `waived` | Yes | You explicitly waived the top-level red-test gate. | Waiver reason. |
| `blocked` | No | Red-test state is not acceptable yet. | What must happen before implementation. |

Visual gate:

```mermaid
flowchart TB
    Todo["Layer todo"]
    Ownership{"test_ownership"}
    HumanWritten["human-written"]
    AgentWritten["agent-written-after-approval"]
    Waived["waived"]
    RedState{"red_gate_state"}
    Allowed["Implementor may proceed"]
    Blocked["Blocked"]

    Todo --> Ownership
    Ownership --> HumanWritten
    Ownership --> AgentWritten
    Ownership --> Waived
    HumanWritten --> RedState
    AgentWritten --> ApproveGherkin["Human approves Gherkin"]
    ApproveGherkin --> AuthorTest["Agent writes and runs top-level test"]
    AuthorTest --> RunSuite["Conductor runs configured full suite"]
    RunSuite --> ConfirmRed["Human chooses red/green decision in gate"]
    ConfirmRed --> RedState
    Waived --> RedState
    RedState -->|"observed-red"| Allowed
    RedState -->|"not-run-human-approved"| Allowed
    RedState -->|"already-passing-human-approved"| Allowed
    RedState -->|"waived"| Allowed
    RedState -->|"blocked"| Blocked
```

#### Suggested Layer Todo Frontmatter Shapes

Human-written test, observed red:

```yaml
---
workflow: layered-tdd
owner: human
status: ready-for-implementation
selected_layer: api-contract
test_ownership: human-written
red_gate_state: observed-red
---
```

After the agent has authored the approved test, full-suite evidence still awaits human review:

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-test-gate
selected_layer: persistence
test_ownership: agent-written-after-approval
red_gate_state: blocked
---
```

#### Reproducible Agent-Written Test Route

Use this route when you approve the Gherkin but want the agent to author the
top-level test before any production code is touched.

```mermaid
flowchart LR
    A["Approve Gherkin and agent test ownership"] --> B["Choose: agent authors top-level test"]
    B --> Prep["Script records decision + marks test tasks in progress"]
    Prep --> Context["Narrow test contract + source projection"]
    Context --> C["Agent writes only the approved test"]
    C --> Validate["Script validates changed paths + marks tasks done"]
    Validate --> D["Conductor runs full suite and stores bounded evidence"]
    D --> E["Human chooses red/green evidence decision + comment"]
    E --> F["Script persists state and snapshots the layer boundary"]
    F --> G["Implementation starts"]
```

| Step | Artifact state / action | Production code allowed? |
| --- | --- | --- |
| 1 | Approve Gherkin and select `agent-written-after-approval` in the layer-todo gate. Add any caveat as the optional gate comment. | No |
| 2 | A script records agent test ownership and the optional comment, then marks only exact `Type: top-level-test` Task Board rows in progress. A legacy `Type: test` row is accepted only when exactly one exists. Ambiguity fails closed to human revision. | No |
| 3 | The agent receives only the red gate, behavior contract, boundary, test task rows, bounded source, and exact original request. It changes only approved tests; it does not edit the todo or inspect Git state. | No |
| 4 | A script compares the pre-author snapshot with the working tree. Every change must be a reported, in-boundary, non-read-only test path before the script marks the test task rows done. A checkpoint is valid only with no file changes. | No |
| 5 | Conductor runs the configured full suite (`test_command`), keeps complete output in an evidence file, and records a bounded summary in `## Evidence`. | No |
| 6 | At the evidence gate, choose whether the recorded result is expected red or acceptable existing coverage. The decision recorder updates frontmatter and `## Decision Log`. | Yes |

Do not set `observed-red` manually. The deterministic full-suite verifier records the result and the decision recorder writes the allowed gate state only after the human approves it.

`red_gate_state: blocked` is correct in the example: it means production code
is blocked pending full-suite evidence and the human decision, not that the
agent is blocked from authoring the already approved top-level test.

Waived top-level red test:

```yaml
---
workflow: layered-tdd
owner: human
status: ready-for-implementation
selected_layer: logging
test_ownership: waived
red_gate_state: waived
---
```

Checkpoint:

```yaml
---
workflow: layered-tdd
owner: human
status: checkpoint
selected_layer: domain-rules
test_ownership: human-written
red_gate_state: observed-red
---
```

### `99-final-review.md`

The end-of-slice review and memory decision artifact.

```yaml
---
workflow: layered-tdd
owner: human
status: needs-human-memory-decision
memory_decision:
---
```

| Field | Values | Use this when... | Next dashboard choice |
| --- | --- | --- | --- |
| `status` | `needs-human-memory-decision` | Final review exists and memory candidates need approval/skip/revision. | Capture, skip, or revise. |
| `status` | `complete` | The slice is done and memory decision is handled. | Workflow should terminate. |
| `status` | `revising` | Final summary, risks, or candidates need changes. | Choose revise final review. |
| `memory_decision` | `capture` | You marked specific candidates as approved. | Choose capture. |
| `memory_decision` | `skip` | You do not want memory capture. | Choose skip. |
| `memory_decision` | `revise` | You want the final reviewer to rewrite candidates or risks. | Choose revise. |

Memory capture only uses Markdown memory. It does not use Engram or MCP memory tools.

| Memory inputs available? | What happens when you choose capture |
| --- | --- |
| `memory_vault` and `memory_project` are provided, vault exists. | Approved candidates are staged or promoted under the project memory folder. |
| Project folder does not exist but vault exists. | Creates only the minimal `inbox/staged-observations.md` path. |
| Vault or project input is missing. | Capture is skipped and final review records why. |

## Stage-By-Stage Playbook

### 1. Preflight

```mermaid
flowchart LR
    Tests["test_command"] --> Lint["lint_command"]
    Lint --> Security["security_command"]
    Security --> Graphify{"Refresh Graphify?"}
    Graphify --> Requirements["requirements_griller"]
    Tests -- fail --> Gate["preflight_failure_gate"]
    Lint -- fail --> Gate
    Security -- fail --> Gate
```

| If... | Do this |
| --- | --- |
| A command fails because the repo is genuinely broken. | Fix it, then choose retry. |
| The command name is wrong for this repo. | Restart with overridden `test_command`, `lint_command`, or `security_command`. |
| You knowingly accept the existing failure. | Choose continue with waiver. |

### 2. Requirements

```mermaid
flowchart LR
    Griller["requirements_griller"] --> Artifact{"Artifact written"}
    Artifact -->|"multiple_slices=true"| SliceSelection["slice-selection.md"]
    Artifact -->|"multiple_slices=false"| Requirements["00-requirements.md"]
```

What to do:

| File | Your action |
| --- | --- |
| `slice-selection.md` | Select one slice only. Do not try to implement all slices in one run. |
| `00-requirements.md` | Confirm goal, blockers, assumptions, and out-of-scope work. |

### 3. Layer Map

```mermaid
flowchart LR
    Requirements["confirmed requirements"] --> Mapper["layer_mapper"]
    Mapper --> Map["01-layer-map.md"]
    Mapper --> Todos["layers/*.todo.md skeletons"]
    Map --> Human["human selects one layer"]
```

What to check:

| Check | Why |
| --- | --- |
| Layers match real repo boundaries. | Prevents one layer from becoming a full feature. |
| Recommended order is sensible. | Helps avoid hidden dependencies. |
| Each layer has a todo filename. | The next agent needs a concrete target. |
| Removed layers are deleted or superseded. | Prevents stale todos from being selected later. |

### 4. Layer Todo And Red-Test Gate

```mermaid
sequenceDiagram
    participant H as Human
    participant G as Todo generator
    participant A as Artifact
    participant I as Implementor

    H->>A: Select layer in 01-layer-map.md
    H->>G: Choose selected gate
    G->>A: Write layer todo with Gherkin and gate state
    H->>A: Approve or edit test ownership and red gate
    H->>I: Choose layer todo approved gate
```

What must be clear before implementation:

| Required item | Where |
| --- | --- |
| Gherkin or equivalent top-level behavior contract. | Layer todo body. |
| Implementation boundary. | Layer todo body. |
| `test_ownership`. | Layer todo frontmatter. |
| `red_gate_state`. | Layer todo frontmatter. |
| Evidence or waiver reason. | Layer todo body. |

### 5. Implementation, Verification, Review

```mermaid
flowchart LR
    Snapshot["pre-layer snapshot"] --> Implementor["implementor"]
    Implementor --> Checkpoint{"checkpoint_required?"}
    Checkpoint -- "yes" --> CheckpointGate["checkpoint gate"]
    Checkpoint -- "no" --> Tests["test_command"]
    CheckpointGate -->|"proceed"| Tests
    CheckpointGate -->|"revise layer todo"| Todo["layer_todo_generator"]
    CheckpointGate -->|"select other layer"| Select["layer_selection_gate"]
    Tests --> Lint["lint_command"]
    Lint --> Security["security_command"]
    Security --> Diff["bounded layer-only patch"]
    Diff --> Reviewer["layer_reviewer"]
    Reviewer --> Approval["layer_approval_gate"]
```

Layer approval choices:

| Choice | Route | Use when... |
| --- | --- | --- |
| Approve this layer and choose another layer. | `layer_selection_gate` | More layers remain. |
| Approve this layer and run final slice review. | `final_reviewer` | This was the last useful layer. |
| Send the layer back for focused fixes. | `implementor` | Review found bounded issues. |
| Stop this slice. | `stopped` | You want to pause or abandon. |

### 6. Final Review And Memory

```mermaid
flowchart LR
    FinalReview["99-final-review.md"] --> Decision{"memory gate"}
    Decision -->|"capture"| Capturer["memory_capturer"]
    Decision -->|"skip"| Done["done"]
    Decision -->|"revise"| FinalReview
    Capturer --> Done
```

Before choosing capture:

| Check | Reason |
| --- | --- |
| Candidates are durable, not task-local noise. | Memory should help future work. |
| Sensitive values are excluded. | Capturer must not persist secrets or raw transcripts. |
| Approved candidates are clearly marked. | Capturer captures only human-approved candidates. |
| Memory inputs were provided. | Otherwise capture records that memory was unavailable. |

## Quick Recovery Matrix

| You see... | Open... | Set/check... | Choose... |
| --- | --- | --- | --- |
| "I forgot where I am." | Latest `.github/plans/<slug>/` folder | Highest-numbered artifact with `owner: human`. | Gate matching that artifact. |
| Multiple slice ideas. | `slice-selection.md` | `status: selected`, `selected_slice: <one slice>`. | Selected fresh slice run. |
| Requirements feel wrong. | `00-requirements.md` | `status: revising`, add comments/answers. | Revise requirements; the same artifact is updated in place. |
| No idea which layer is next. | `01-layer-map.md` | `selected_layer: <todo filename>`. | Selected next layer. |
| Agent wants to implement but tests are unclear. | Active layer todo | `test_ownership`, `red_gate_state`, evidence. | Revise or approve layer todo. |
| New behavior appeared during implementation. | Active layer todo | `status: checkpoint`, checkpoint notes. | Checkpoint gate route. |
| Verification failed after implementation. | Active layer todo and dashboard output | Review notes and failures. | Request focused fixes. |
| Slice seems done. | `99-final-review.md` | `status: needs-human-memory-decision`, approved candidates. | Capture or skip. |

## Minimal Artifact Templates

### Requirements

```markdown
---
workflow: layered-tdd
owner: human
status: needs-human-confirmation
---

# Requirements

## Scope Map

| Area | Decision |
| --- | --- |
| Slice goal |  |
| In scope |  |
| Out of scope |  |
| Dependencies |  |
| Non-goals |  |

## Blockers

| Question | Why it blocks | Owner | Answer/status |
| --- | --- | --- | --- |

## Assumptions

| Assumption | Confidence | Risk if wrong | Validation path |
| --- | --- | --- | --- |

## Memory Used

| Source | State | Impact |
| --- | --- | --- |

## Decision Log

| Decision | Human comment | Timestamp |
| --- | --- | --- |
```

### Layer Map

````markdown
---
workflow: layered-tdd
owner: human
status: needs-human-layer-selection
selected_layer:
---

# Layer Map

## Layer Flow

```mermaid
flowchart LR
    L1["layer-1"] --> L2["layer-2"]
```

## Layer Matrix

| Order | Layer | Todo | Boundary | Dependency | Risk |
| --- | --- | --- | --- | --- | --- |

## Selection Board

| Layer | Why now | Ready? | Blocking notes |
| --- | --- | --- | --- |
````

### Layer Todo

````markdown
---
workflow: layered-tdd
owner: human
status: needs-human-test-gate
selected_layer:
test_ownership:
red_gate_state: blocked
---

# Layer Todo

## Red-Test Gate

| Field | Value |
| --- | --- |
| State | `blocked` |
| Evidence command |  |
| Observed result |  |
| Waiver/approval reason |  |
| May implement? | no |

## Decision Log

| Decision | Human comment | Command/evidence | Timestamp |
| --- | --- | --- | --- |

## Evidence

| Check | Command | Exit code | Result | Evidence path |
| --- | --- | --- | --- | --- |

## Behavior Contract

```gherkin
Feature:
  Scenario:
    Given
    When
    Then
```

## Implementation Boundary

| Area | Allowed? | Notes |
| --- | --- | --- |
| Production files |  |  |
| Top-level tests | read-only by default |  |
| Internal tests | yes, inside boundary |  |

## Task Board

| Task | Type | Owner | Status | Notes |
| --- | --- | --- | --- | --- |

## Risk Board

| Risk | Trigger | Mitigation | Checkpoint condition |
| --- | --- | --- | --- |
````

### Final Review

````markdown
---
workflow: layered-tdd
owner: human
status: needs-human-memory-decision
memory_decision:
---

# Final Review

## Completion Dashboard

| Field | Value |
| --- | --- |
| Status | `needs-human-memory-decision` |
| Owner | `human` |
| Slice goal |  |
| Layers completed |  |
| Verification result |  |
| Residual risk count |  |
| Memory candidate count |  |
| Next decision | Capture, skip, or revise. |

## Slice Flow

```mermaid
flowchart LR
    Requirements["00-requirements.md"] --> Layers["layers/*.todo.md"]
    Layers --> Review["99-final-review.md"]
```

## Layer Completion Matrix

| Layer | Todo | Status | Red gate | Verification | Approval notes |
| --- | --- | --- | --- | --- | --- |

## Verification Matrix

| Command | Result | Evidence |
| --- | --- | --- |

## Memory Candidates

| Candidate | Observation | Durability | Destination | Approved? |
| --- | --- | --- | --- | --- |
````

## Validate The Workflow

```bash
conductor validate workflows/conductor/layered-tdd.yaml
conductor run workflows/conductor/layered-tdd.yaml \
  --dry-run \
  --input request="Smoke test the workflow"
```

## References

| Reference | Use it for |
| --- | --- |
| [`guide.md`](guide.md) | Installation, synchronization, and day-to-day usage. |
| [`design-notes.md`](design-notes.md) | Original design notes behind the layered workflow. |
| `layered-tdd.yaml` | Exact Conductor routes and gate options. |
