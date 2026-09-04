#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOKEN_PATTERNS = {
    "input_tokens": re.compile(r"^\s*Input:\s+([\d,]+)\s+tokens\s*$", re.MULTILINE),
    "output_tokens": re.compile(r"^\s*Output:\s+([\d,]+)\s+tokens\s*$", re.MULTILINE),
    "total_tokens": re.compile(r"^\s*Total:\s+([\d,]+)\s+tokens\s*$", re.MULTILINE),
}
COST_LINE = re.compile(r"^\s{2}([A-Za-z0-9_.$-]+):\s+\$([\d.]+)(?:\s+\(\d+%\))?\s*$", re.MULTILINE)
TOTAL_COST = re.compile(r"^\s*Total:\s+\$([\d.]+)\s*$", re.MULTILINE)
EVENT_LOG_PATH = re.compile(r"(?P<path>/[^\s'\"]+\.events\.jsonl)")
LAYER_SELECTION_GATES = {"layer_selection_gate", "layer_map_revision_gate"}
MODEL_AGENT_TYPE = "agent"


def last_number(pattern: re.Pattern[str], text: str, cast):
    matches = pattern.findall(text)
    if not matches:
        return None
    return cast(matches[-1].replace(",", ""))


def encoded_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(rendered.encode("utf-8"))


def optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def optional_number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def resolve_events_log(explicit: Path | None, run_log: Path, run_text: str) -> Path | None:
    if explicit is not None:
        return explicit

    adjacent = Path(f"{run_log}.events.jsonl")
    if adjacent.is_file():
        return adjacent

    for match in reversed(EVENT_LOG_PATH.findall(run_text)):
        candidate = Path(match)
        if candidate.is_file():
            return candidate
    return None


def _empty_telemetry(reason: str, source: Path | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "coverage": "none",
        "reason": reason,
        "source": str(source) if source else None,
        "run_id": None,
        "conductor_version": None,
        "events_parsed": 0,
        "malformed_event_lines": 0,
        "availability": {
            "input_tokens": False,
            "output_tokens": False,
            "cached_input_tokens": False,
            "cache_write_tokens": False,
            "reasoning_tokens": False,
            "cost_usd": False,
            "prompt_bytes": False,
            "tool_argument_bytes": False,
            "tool_output_bytes": False,
            "script_output_bytes": False,
            "artifact_bytes": False,
            "graphify_query_count": False,
        },
        "summary": {},
        "model_invocations": [],
        "scripts": [],
        "gates": [],
        "routes": [],
        "retries": [],
        "by_agent": {},
        "by_layer": {},
    }


def parse_event_telemetry(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _empty_telemetry("No Conductor event log was provided or found beside the run log.")
    if not path.is_file():
        return _empty_telemetry("The Conductor event log does not exist.", path)

    events: list[dict[str, Any]] = []
    malformed = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(event, dict) and isinstance(event.get("type"), str):
                events.append(event)
            else:
                malformed += 1

    if not events:
        telemetry = _empty_telemetry("The Conductor event log contained no valid events.", path)
        telemetry["malformed_event_lines"] = malformed
        return telemetry

    agent_config: dict[str, dict[str, Any]] = {}
    open_invocations: dict[str, dict[str, Any]] = {}
    latest_invocation: dict[str, dict[str, Any]] = {}
    invocations: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    pending_entered_from: dict[str, str] = {}
    active_layer: str | None = None
    run_id: str | None = None
    conductor_version: str | None = None
    event_type_counts: Counter[str] = Counter()

    for event in events:
        event_type = event["type"]
        event_type_counts[event_type] += 1
        timestamp = optional_number(event.get("timestamp"))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        agent_name = data.get("agent_name") if isinstance(data.get("agent_name"), str) else None

        if event_type == "workflow_started":
            system = data.get("system") if isinstance(data.get("system"), dict) else {}
            run_id = data.get("run_id") or system.get("run_id")
            conductor_version = system.get("conductor_version") or data.get("version")
            for configured in data.get("agents", []):
                if not isinstance(configured, dict) or not isinstance(configured.get("name"), str):
                    continue
                agent_config[configured["name"]] = {
                    "agent_type": configured.get("type"),
                    "requested_model": configured.get("model"),
                    "provider": configured.get("provider_name"),
                    "reasoning_effort": configured.get("reasoning_effort"),
                }
            continue

        if event_type == "gate_resolved" and agent_name:
            additional_input = data.get("additional_input")
            if not isinstance(additional_input, dict):
                additional_input = {}
            gates.append(
                {
                    "agent": agent_name,
                    "layer_id": active_layer,
                    "selected_option": data.get("selected_option"),
                    "route": data.get("route"),
                    "additional_input_fields": sorted(str(key) for key in additional_input),
                    "additional_input_bytes": encoded_bytes(additional_input),
                    "resolved_at": timestamp,
                }
            )
            if agent_name in LAYER_SELECTION_GATES:
                selected_layer = additional_input.get("selected_layer")
                if isinstance(selected_layer, str) and selected_layer:
                    active_layer = selected_layer
            elif (
                agent_name == "layer_approval_gate"
                and data.get("selected_option") != "request_fixes"
            ):
                active_layer = None
            continue

        if event_type == "agent_started" and agent_name:
            agent_type = data.get("agent_type") or agent_config.get(agent_name, {}).get("agent_type")
            if agent_type != MODEL_AGENT_TYPE:
                continue
            iteration = optional_int(data.get("iteration")) or 1
            config = agent_config.get(agent_name, {})
            invocation = {
                "invocation_id": f"{agent_name}#{iteration}",
                "sequence": len(invocations) + 1,
                "agent": agent_name,
                "iteration": iteration,
                "layer_id": active_layer,
                "agent_type": MODEL_AGENT_TYPE,
                "provider": config.get("provider"),
                "requested_model": config.get("requested_model"),
                "actual_model": None,
                "reasoning_effort": config.get("reasoning_effort"),
                "started_at": timestamp,
                "completed_at": None,
                "elapsed_seconds": None,
                "status": "started",
                "tokens": {
                    "input": None,
                    "cached_input": None,
                    "cache_write": None,
                    "output": None,
                    "reasoning": None,
                    "total": None,
                },
                "cost_usd": None,
                "prompt_bytes": None,
                "tool_calls": 0,
                "tool_argument_bytes": 0,
                "tool_output_bytes": 0,
                "tool_output_original_chars": 0,
                "tool_output_kept_chars": 0,
                "tool_output_truncated_count": 0,
                "tool_names": {},
                "entered_from": pending_entered_from.pop(agent_name, None),
                "route_to": None,
                "retry_count": 0,
            }
            invocations.append(invocation)
            open_invocations[agent_name] = invocation
            latest_invocation[agent_name] = invocation
            continue

        invocation = open_invocations.get(agent_name) if agent_name else None

        if event_type == "agent_prompt_rendered" and invocation is not None:
            prompt = data.get("rendered_prompt", data.get("prompt"))
            invocation["prompt_bytes"] = encoded_bytes(prompt)
            continue

        if event_type == "agent_tool_start" and invocation is not None:
            tool_name = data.get("tool_name")
            tool_key = str(tool_name) if tool_name else "unknown"
            tool_names = Counter(invocation["tool_names"])
            tool_names[tool_key] += 1
            invocation["tool_names"] = dict(sorted(tool_names.items()))
            invocation["tool_calls"] += 1
            invocation["tool_argument_bytes"] += encoded_bytes(data.get("arguments"))
            continue

        if event_type == "agent_tool_complete" and invocation is not None:
            invocation["tool_output_bytes"] += encoded_bytes(data.get("result"))
            continue

        if event_type == "agent_tool_output_truncated" and invocation is not None:
            invocation["tool_output_truncated_count"] += 1
            invocation["tool_output_original_chars"] += optional_int(data.get("original_chars")) or 0
            invocation["tool_output_kept_chars"] += optional_int(data.get("kept_chars")) or 0
            continue

        if event_type in {"agent_retry", "provider_retry"} and agent_name:
            target = invocation or latest_invocation.get(agent_name)
            retry = {
                "agent": agent_name,
                "layer_id": active_layer,
                "event_type": event_type,
                "attempt": optional_int(data.get("attempt")),
                "max_attempts": optional_int(data.get("max_attempts")),
                "reason": data.get("reason") or data.get("error_type"),
                "timestamp": timestamp,
            }
            retries.append(retry)
            if target is not None:
                target["retry_count"] += 1
            continue

        if event_type == "agent_completed" and invocation is not None:
            invocation["completed_at"] = timestamp
            invocation["elapsed_seconds"] = optional_number(data.get("elapsed"))
            invocation["status"] = "completed"
            invocation["actual_model"] = data.get("model")
            invocation["tokens"] = {
                "input": optional_int(data.get("input_tokens")),
                "cached_input": optional_int(data.get("cache_read_tokens")),
                "cache_write": optional_int(data.get("cache_write_tokens")),
                "output": optional_int(data.get("output_tokens")),
                "reasoning": optional_int(data.get("reasoning_tokens")),
                "total": optional_int(data.get("tokens")),
            }
            invocation["cost_usd"] = optional_number(data.get("cost_usd"))
            output = data.get("output")
            if isinstance(output, dict) and not active_layer:
                selected_layer = output.get("selected_layer")
                if isinstance(selected_layer, str) and selected_layer:
                    invocation["layer_id"] = selected_layer
                    active_layer = selected_layer
            open_invocations.pop(agent_name, None)
            continue

        if event_type == "agent_failed" and agent_name:
            target = invocation or latest_invocation.get(agent_name)
            if target is not None:
                target["completed_at"] = timestamp
                target["elapsed_seconds"] = optional_number(data.get("elapsed"))
                target["status"] = "failed"
                target["failure_type"] = data.get("error_type")
                open_invocations.pop(agent_name, None)
            continue

        if event_type == "script_completed" and agent_name:
            scripts.append(
                {
                    "agent": agent_name,
                    "layer_id": active_layer,
                    "completed_at": timestamp,
                    "elapsed_seconds": optional_number(data.get("elapsed")),
                    "exit_code": optional_int(data.get("exit_code")),
                    "stdin_bytes": optional_int(data.get("stdin_bytes")),
                    "stdout_bytes": encoded_bytes(data.get("stdout")),
                    "stderr_bytes": encoded_bytes(data.get("stderr")),
                }
            )
            continue

        if event_type == "route_taken":
            from_agent = data.get("from_agent")
            to_agent = data.get("to_agent")
            route = {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "layer_id": active_layer,
                "timestamp": timestamp,
            }
            routes.append(route)
            if isinstance(from_agent, str) and from_agent in latest_invocation:
                latest_invocation[from_agent]["route_to"] = to_agent
            if isinstance(to_agent, str) and isinstance(from_agent, str):
                pending_entered_from[to_agent] = from_agent

    token_fields = ("input", "cached_input", "cache_write", "output", "reasoning", "total")

    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        totals = {field: 0 for field in token_fields}
        available = {field: False for field in token_fields}
        total_cost = 0.0
        cost_available = False
        for row in rows:
            for field in token_fields:
                value = row["tokens"].get(field)
                if isinstance(value, int):
                    totals[field] += value
                    available[field] = True
            if isinstance(row.get("cost_usd"), (int, float)):
                total_cost += float(row["cost_usd"])
                cost_available = True
        return {
            "model_invocations": len(rows),
            "input_tokens": totals["input"] if available["input"] else None,
            "cached_input_tokens": totals["cached_input"] if available["cached_input"] else None,
            "cache_write_tokens": totals["cache_write"] if available["cache_write"] else None,
            "output_tokens": totals["output"] if available["output"] else None,
            "reasoning_tokens": totals["reasoning"] if available["reasoning"] else None,
            "total_tokens": totals["total"] if available["total"] else None,
            "cost_usd": total_cost if cost_available else None,
            "prompt_bytes": sum(row.get("prompt_bytes") or 0 for row in rows),
            "tool_calls": sum(row["tool_calls"] for row in rows),
            "tool_argument_bytes": sum(row["tool_argument_bytes"] for row in rows),
            "tool_output_bytes": sum(row["tool_output_bytes"] for row in rows),
            "tool_output_truncated_count": sum(row["tool_output_truncated_count"] for row in rows),
            "retry_count": sum(row["retry_count"] for row in rows),
        }

    by_agent_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_layer_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for invocation in invocations:
        by_agent_rows[invocation["agent"]].append(invocation)
        by_layer_rows[invocation["layer_id"] or "unscoped"].append(invocation)

    summary = aggregate(invocations)
    summary.update(
        {
            "script_executions": len(scripts),
            "script_stdout_bytes": sum(row["stdout_bytes"] for row in scripts),
            "script_stderr_bytes": sum(row["stderr_bytes"] for row in scripts),
            "script_output_bytes": sum(
                row["stdout_bytes"] + row["stderr_bytes"] for row in scripts
            ),
            "gate_resolutions": len(gates),
            "routes_taken": len(routes),
            "retry_events": len(retries),
            "layers_observed": sorted(key for key in by_layer_rows if key != "unscoped"),
        }
    )

    availability = {
        "input_tokens": any(row["tokens"]["input"] is not None for row in invocations),
        "output_tokens": any(row["tokens"]["output"] is not None for row in invocations),
        "cached_input_tokens": any(
            row["tokens"]["cached_input"] is not None for row in invocations
        ),
        "cache_write_tokens": any(
            row["tokens"]["cache_write"] is not None for row in invocations
        ),
        "reasoning_tokens": any(row["tokens"]["reasoning"] is not None for row in invocations),
        "cost_usd": any(row["cost_usd"] is not None for row in invocations),
        "prompt_bytes": any(row["prompt_bytes"] is not None for row in invocations),
        "tool_argument_bytes": event_type_counts["agent_tool_start"] > 0,
        "tool_output_bytes": event_type_counts["agent_tool_complete"] > 0,
        "script_output_bytes": event_type_counts["script_completed"] > 0,
        "artifact_bytes": False,
        "graphify_query_count": False,
    }
    unavailable = [name for name, present in availability.items() if not present]
    coverage = "partial" if malformed or unavailable else "complete"
    coverage_reasons: list[str] = []
    if unavailable:
        coverage_reasons.append(
            "Some requested fields are not emitted authoritatively by this Conductor/provider event log."
        )
    if malformed:
        coverage_reasons.append(f"Skipped {malformed} malformed event log line(s).")

    return {
        "status": "available",
        "coverage": coverage,
        "reason": " ".join(coverage_reasons) or None,
        "source": str(path.resolve()),
        "run_id": str(run_id) if run_id is not None else None,
        "conductor_version": str(conductor_version) if conductor_version is not None else None,
        "events_parsed": len(events),
        "malformed_event_lines": malformed,
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "availability": availability,
        "unavailable_fields": unavailable,
        "summary": summary,
        "model_invocations": invocations,
        "scripts": scripts,
        "gates": gates,
        "routes": routes,
        "retries": retries,
        "by_agent": {key: aggregate(rows) for key, rows in sorted(by_agent_rows.items())},
        "by_layer": {key: aggregate(rows) for key, rows in sorted(by_layer_rows.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a Conductor benchmark summary into JSON.")
    parser.add_argument("--variant", required=True)
    parser.add_argument("--run-log", required=True, type=Path)
    parser.add_argument(
        "--events-log",
        type=Path,
        help="Conductor .events.jsonl log; defaults to <run-log>.events.jsonl or a path printed in the run log.",
    )
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    text = args.run_log.read_text(encoding="utf-8", errors="replace")
    project = args.project.resolve()
    case = json.loads((project / ".benchmark" / "case.json").read_text(encoding="utf-8"))
    evaluation_path = project / ".benchmark" / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8")) if evaluation_path.exists() else None

    metrics = {name: last_number(pattern, text, int) for name, pattern in TOKEN_PATTERNS.items()}
    metrics["total_cost_usd"] = last_number(TOTAL_COST, text, float)

    agent_costs: dict[str, float] = {}
    invocation_count = 0
    for agent, cost in COST_LINE.findall(text):
        if agent == "Total":
            continue
        invocation_count += 1
        agent_costs[agent] = agent_costs.get(agent, 0.0) + float(cost)

    metrics["model_invocations"] = invocation_count
    metrics["input_tokens_per_invocation"] = (
        metrics["input_tokens"] / invocation_count if metrics["input_tokens"] is not None and invocation_count else None
    )
    metrics["cost_per_invocation_usd"] = (
        metrics["total_cost_usd"] / invocation_count if metrics["total_cost_usd"] is not None and invocation_count else None
    )
    expected_layers = int(case["expected_layers"])
    run_kind = str(case.get("run_kind", "full"))
    run_layers = case.get("run_layers")
    if not isinstance(run_layers, list) or not all(isinstance(layer, str) for layer in run_layers):
        run_layers = []
    run_layer_count = len(run_layers) or expected_layers
    claim_eligible = bool(case.get("claim_eligible", run_kind == "full"))
    metrics["input_tokens_per_expected_layer"] = (
        metrics["input_tokens"] / expected_layers if metrics["input_tokens"] is not None else None
    )
    metrics["cost_per_expected_layer_usd"] = (
        metrics["total_cost_usd"] / expected_layers if metrics["total_cost_usd"] is not None else None
    )
    metrics["input_tokens_per_run_layer"] = (
        metrics["input_tokens"] / run_layer_count if metrics["input_tokens"] is not None else None
    )
    metrics["cost_per_run_layer_usd"] = (
        metrics["total_cost_usd"] / run_layer_count
        if metrics["total_cost_usd"] is not None
        else None
    )

    events_log = resolve_events_log(args.events_log, args.run_log, text)
    telemetry = parse_event_telemetry(events_log)
    telemetry_summary = telemetry["summary"]
    metrics["telemetry_model_invocations"] = telemetry_summary.get("model_invocations")
    metrics["telemetry_input_tokens"] = telemetry_summary.get("input_tokens")
    metrics["telemetry_output_tokens"] = telemetry_summary.get("output_tokens")
    metrics["telemetry_prompt_bytes"] = telemetry_summary.get("prompt_bytes")
    metrics["telemetry_tool_output_bytes"] = telemetry_summary.get("tool_output_bytes")
    metrics["telemetry_script_output_bytes"] = telemetry_summary.get("script_output_bytes")
    metrics["telemetry_retry_count"] = telemetry_summary.get("retry_count")
    event_invocations = telemetry_summary.get("model_invocations")
    if isinstance(event_invocations, int) and event_invocations > 0:
        metrics["summary_cost_rows"] = invocation_count
        metrics["model_invocations"] = event_invocations
        metrics["input_tokens_per_invocation"] = (
            metrics["input_tokens"] / event_invocations
            if metrics["input_tokens"] is not None
            else None
        )

    telemetry["reconciliation"] = {
        "summary_input_tokens": metrics["input_tokens"],
        "event_input_tokens": telemetry_summary.get("input_tokens"),
        "input_token_difference": (
            metrics["input_tokens"] - telemetry_summary["input_tokens"]
            if metrics["input_tokens"] is not None
            and isinstance(telemetry_summary.get("input_tokens"), int)
            else None
        ),
        "summary_output_tokens": metrics["output_tokens"],
        "event_output_tokens": telemetry_summary.get("output_tokens"),
        "output_token_difference": (
            metrics["output_tokens"] - telemetry_summary["output_tokens"]
            if metrics["output_tokens"] is not None
            and isinstance(telemetry_summary.get("output_tokens"), int)
            else None
        ),
    }

    result = {
        "variant": args.variant,
        "case": case["case"],
        "expected_layers": expected_layers,
        "benchmark_scope": {
            "kind": run_kind,
            "run_layers": run_layers,
            "run_layer_count": run_layer_count,
            "claim_eligible": claim_eligible,
        },
        "test_command": case["test_command"],
        "graphify": case["graphify"],
        "metrics": metrics,
        "agent_costs_usd": agent_costs,
        "telemetry": telemetry,
        "evaluation": evaluation,
        "notes": args.notes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
