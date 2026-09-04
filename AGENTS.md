# Conductor Layered TDD agent guidance

## Source of truth

- `bundle/workflows/conductor/` is the only canonical workflow tree.
- `bundle/skills/` contains every skill distributed by `ltdd`.
- Do not create a second runnable or embedded copy of the workflow.

## Required validation

When workflow, prompt, recorder, installer, or skill content changes:

1. Run `GOCACHE=/tmp/ltdd-go-build go test ./...`.
2. Install into a temporary repository with `go run ./cmd/ltdd install --target <path> --memory-skills`.
3. Compare the installed workflow tree with `bundle/workflows/conductor/`.
4. Run `conductor validate <path>/workflows/conductor/layered-tdd.yaml` when Conductor is available.
5. Run `git diff --check`.

Preserve target-local edits by default. `--force` is the explicit replacement boundary.
