# Benchmark Agent Guidance

This repository is a deterministic layered-TDD benchmark fixture.

- Implement only the request stored in `.benchmark/request.md`.
- Use exactly the requested layer IDs and order.
- Do not modify `.benchmark/`, `workflows/`, `Makefile`, `go.mod`, or `internal/legacy/`.
- Do not add dependencies.
- Run the configured full suite for evidence; do not replace it with a targeted command.
- Keep generated plan artifacts under `.github/plans/`.
