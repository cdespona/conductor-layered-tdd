#!/usr/bin/env python3
"""Run the fixed layered-TDD benchmark gate playbook without timing races."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import pexpect


TIMEOUT_SECONDS = 900
UNEXPECTED_GATES = (
    "agent_test_checkpoint_gate",
    "checkpoint_gate",
    "preflight_failure_gate",
    "graphify_refresh_failure_gate",
)
FULL_LAYERS = ("L10-domain", "L20-service", "L30-http")


class UnexpectedGateError(RuntimeError):
    def __init__(self, expected_gate: str, unexpected_gate: str) -> None:
        self.expected_gate = expected_gate
        self.unexpected_gate = unexpected_gate
        super().__init__(
            f"unexpected gate before {expected_gate}: {unexpected_gate}"
        )


def benchmark_scope(case: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    run_kind = str(case.get("run_kind", "full"))
    raw_layers = case.get("run_layers", FULL_LAYERS)
    if not isinstance(raw_layers, (list, tuple)) or not all(
        isinstance(layer, str) for layer in raw_layers
    ):
        raise ValueError("run_layers must be an array of layer identifiers")
    run_layers = tuple(raw_layers)
    if run_kind not in {"full", "one-layer-diagnostic"}:
        raise ValueError(f"unsupported benchmark run_kind: {run_kind}")
    if run_kind == "full" and run_layers != FULL_LAYERS:
        raise ValueError(f"full benchmark must run layers in order: {FULL_LAYERS}")
    if run_kind == "one-layer-diagnostic" and run_layers != ("L10-domain",):
        raise ValueError("one-layer diagnostic currently supports only L10-domain")
    return run_kind, run_layers


def layer_approval_response(run_kind: str, index: int, layer_count: int) -> tuple[int, str]:
    if run_kind == "one-layer-diagnostic":
        return 4, ""
    return (2 if index == layer_count - 1 else 1), "feedback"


def preserve_event_log(
    run_id: str,
    destination: Path,
    conductor_log_dir: Path | None = None,
) -> Path | None:
    log_dir = conductor_log_dir or (Path(tempfile.gettempdir()) / "conductor")
    event_candidates = sorted(
        log_dir.glob(f"conductor-layered-tdd-*-{run_id}.events.jsonl")
    )
    if not event_candidates:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(event_candidates[-1], destination)
    return destination


def non_comparable_outcome(
    error: UnexpectedGateError,
    run_kind: str,
    run_layers: tuple[str, ...],
) -> dict[str, object]:
    return {
        "status": "non-comparable",
        "claim_eligible": False,
        "reason": "unexpected-human-gate",
        "unexpected_gate": error.unexpected_gate,
        "expected_gate": error.expected_gate,
        "run_kind": run_kind,
        "run_layers": list(run_layers),
    }


def write_outcome(path: Path, outcome: dict[str, object]) -> None:
    path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")


def resolve_gate(child: pexpect.spawn, name: str, option: int, field: str = "", value: str = "") -> None:
    unexpected = "|".join(UNEXPECTED_GATES)
    matched = child.expect(
        [
            rf"Agent: {name} ",
            rf"Agent: (?P<unexpected>{unexpected}) ",
        ]
    )
    if matched == 1:
        raise UnexpectedGateError(
            name, child.match.group("unexpected")
        )
    child.expect(r"Select option \[[0-9/]+\]: ")
    child.sendline(str(option))
    if field:
        child.expect(rf"\r?\n  {field}: ")
        child.sendline(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed layered-TDD benchmark gate playbook.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--run-log", required=True, type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    run_log = args.run_log.resolve()
    try:
        run_log.relative_to(project)
    except ValueError:
        pass
    else:
        parser.error("--run-log must be outside the benchmark project")

    request = (project / ".benchmark" / "request.md").read_text(encoding="utf-8")
    case = json.loads(
        (project / ".benchmark" / "case.json").read_text(encoding="utf-8")
    )
    try:
        run_kind, run_layers = benchmark_scope(case)
    except ValueError as error:
        parser.error(str(error))
    run_log.parent.mkdir(parents=True, exist_ok=True)
    debug_log = run_log.with_name(f"{run_log.name}.conductor-debug.log")
    events_log = Path(f"{run_log}.events.jsonl")
    outcome_log = Path(f"{run_log}.outcome.json")
    run_id = secrets.token_hex(8)

    command = [
        "run",
        "workflows/conductor/layered-tdd.yaml",
        "--workspace-instructions",
        "--log-file",
        str(debug_log),
        "--input",
        "task_slug=benchmark-idempotent-cancellation",
        "--input",
        f"test_command={case['test_command']}",
        "--input",
        "lint_command=make lint",
        "--input",
        "security_command=make audit",
        "--input",
        f"request={request}",
    ]

    environment = os.environ.copy()
    environment.setdefault("NO_COLOR", "1")
    environment["CONDUCTOR_RUN_ID"] = run_id
    with run_log.open("w", encoding="utf-8") as log:
        child = pexpect.spawn(
            "conductor",
            command,
            cwd=str(project),
            env=environment,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
        )
        child.logfile_read = log

        try:
            resolve_gate(child, "graphify_update_gate", 3)
            resolve_gate(child, "requirements_gate", 1, "feedback")

            for index, layer in enumerate(run_layers):
                resolve_gate(child, "layer_selection_gate", 1, "selected_layer", layer)
                resolve_gate(child, "layer_todo_gate", 1, "feedback")
                resolve_gate(child, "red_suite_evidence_gate", 1, "feedback")
                approval_option, approval_field = layer_approval_response(
                    run_kind, index, len(run_layers)
                )
                resolve_gate(
                    child, "layer_approval_gate", approval_option, approval_field
                )

            if run_kind == "full":
                resolve_gate(child, "memory_gate", 2, "feedback")
            child.expect(pexpect.EOF)
            child.close()
        except UnexpectedGateError as error:
            outcome = non_comparable_outcome(error, run_kind, run_layers)
            write_outcome(outcome_log, outcome)
            child.close(force=True)
            preserve_event_log(run_id, events_log)
            print(
                f"Benchmark run is non-comparable: {error}. Outcome: {outcome_log}",
                file=sys.stderr,
            )
            raise SystemExit(1) from error

    preserved_events = preserve_event_log(run_id, events_log)
    if preserved_events is None and child.exitstatus == 0:
        raise RuntimeError(
            f"Conductor completed but its event log for run id {run_id} was not found"
        )

    if child.exitstatus != 0:
        raise SystemExit(child.exitstatus or 1)

    print(f"Run log: {run_log}")
    print(f"Conductor events: {events_log}")


if __name__ == "__main__":
    main()
