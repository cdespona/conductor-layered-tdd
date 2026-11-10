# Conductor Layered TDD

`ltdd` installs, safely updates and runs two self-contained Microsoft Conductor workflows that share prompts and scripts: the linear `layered-tdd.yaml` and the star-shaped `layered-star.yaml`. Clearly bounded changes that need no new tests take a short implementation and review route; other requests prepare executable slice acceptance before layered TDD.

## Quick path

```bash
go install github.com/cdespona/conductor-layered-tdd/cmd/ltdd@latest

cd /path/to/your/repository
ltdd install
ltdd validate

ltdd run "Describe the code-changing task"      # star flow (default)
ltdd run --flow linear "Describe the task"      # linear ltdd flow
ltdd resume                                      # resume the last run
```

`ltdd run` wraps `conductor run <installed workflow> --web --workspace-instructions`.
Without a request the star flow asks for the objective at its objective gate.
`--unattended` adds `--skip-gates` and requires a request; an unattended star run
that reaches a non-obvious jump stops with status needs-human and is resumed with
`ltdd resume`. Test, lint and security commands, memory settings and
`graphify_scope` can live in `.ltdd/config.json` (flag > config > workflow
default; `--save` stores the flags passed). Arguments after `--` go to Conductor
unchanged. `ltdd` installs the workflows into the repository where they run;
Conductor itself remains a separate prerequisite.

## Star flow

`layered-star.yaml` keeps the same spokes (decision units of ltdd) but returns to
a central `star_step` after each one. A deterministic router applies
`star/canonical-table.json` and reproduces the ltdd happy path without model
calls; a light hub agent only handles frontier cases (blocked or incoherent
outcomes, free-text redirects, loops). A guard checks invariants I1-I10 before
every spoke, whoever proposes it, and is the only writer of the hash-chained
ledger `.github/plans/<slug>/star-state.json`. Non-obvious hub jumps go through a
confirmation gate that lists the facts they would make stale. See
[`GLOSSARY.md`](GLOSSARY.md) and ADR 0001 for the vocabulary and the decision.

## What is included

```mermaid
flowchart LR
    CLI["ltdd CLI"] --> Bundle["Canonical embedded bundle"]
    Bundle --> Workflow["Workflow · prompts · recorder"]
    Bundle --> Skills["5 core + 3 optional memory skills"]
    Repo["This repository"] --> Docs["Guides · diagrams · design notes"]
    Repo --> Bench["Reproducible benchmark harness"]
```

| Area | Location |
| --- | --- |
| Canonical install bundle | `bundle/` |
| CLI | `cmd/ltdd/` and `internal/` |
| User guide | [`docs/guide.md`](docs/guide.md) |
| Operator guide | [`docs/operator-guide.md`](docs/operator-guide.md) |
| Design history | [`docs/design-notes.md`](docs/design-notes.md) |
| Benchmark harness | [`benchmarks/layered-tdd/`](benchmarks/layered-tdd/) |

## Slice acceptance before layers

```mermaid
flowchart LR
    R[Slice Gherkin] --> D[Support design and human decisions]
    D --> A[Human approves exact scope]
    A --> B[Build AT, DSL, drivers and data setup]
    B --> V[Readiness + regression green, meaningful AT red twice]
    V --> H[Human validates actual changes and evidence]
    H --> L[Existing layer TDD cycles]
    L --> F[Slice acceptance green]
```

The four acceptance responsibilities (scenario, domain DSL, protocol driver and
real system) are separate from architectural implementation layers. Approved
persistence representations may be built early to establish preconditions;
feature business logic remains layer work. The approved test and its support are
protected together. See the [user guide](docs/guide.md#slice-acceptance-preparation)
for commands, evidence and recovery, and the [design handoff](docs/handoffs/2026-11-02-slice-acceptance-before-layers.md)
for the original philosophy and agreed boundaries.

## Install and update

```bash
# Install core workflow skills into the current repository.
ltdd install

# Include Markdown memory skills.
ltdd install --memory-skills

# Use the Claude CLI subscription through Conductor's Claude Agent SDK.
ltdd install --provider claude-agent-sdk

# Target another repository.
ltdd install --target /path/to/repository

# Update files that have not been edited locally.
ltdd sync
```

The default provider is `copilot`. For Claude, install Claude Code and sign in with `claude auth login` before running the workflow. The Claude variant sets `auth_mode: subscription` so Conductor uses the CLI login instead of an API key. It enables Claude Code's native tools and omits Conductor reasoning-effort fields, which this provider does not accept. [Conductor's provider documentation](https://github.com/microsoft/conductor/blob/main/docs/providers/comparison.md) explains the `claude-agent-sdk` provider.

The installer records the provider and file hashes in `.ltdd/manifest.json`. `ltdd sync` reuses the selected provider and updates only files that still match their last installed version. Divergent target files are reported as preserved. Changing providers or replacing local edits requires `--force`.

## Install the CLI

From a source checkout:

```bash
go install ./cmd/ltdd
```

After the first GitHub release:

```bash
curl -fsSL https://raw.githubusercontent.com/cdespona/conductor-layered-tdd/main/scripts/install.sh | bash
```

PowerShell users can run `scripts/install.ps1` from a checkout or download it from the same repository.

## Validate changes

```bash
GOCACHE=/tmp/ltdd-go-build go test ./...
go vet ./...

# Validate an installed target, including Conductor schema validation when available.
ltdd validate --target /path/to/repository
```

The canonical workflow exists only once under `bundle/workflows/conductor/`. Tests install from that exact embedded tree, so runnable and release assets cannot drift into separate copies.
