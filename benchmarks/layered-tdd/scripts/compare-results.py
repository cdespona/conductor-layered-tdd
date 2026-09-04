#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


METRICS = (
    ("input_tokens", "Input tokens", ".0f"),
    ("output_tokens", "Output tokens", ".0f"),
    ("total_cost_usd", "Cost USD", ".4f"),
    ("model_invocations", "Invocations", ".0f"),
    ("input_tokens_per_invocation", "Input / invocation", ".0f"),
    ("input_tokens_per_run_layer", "Input / run layer", ".0f"),
    ("cost_per_run_layer_usd", "Cost / run layer", ".4f"),
    ("telemetry_prompt_bytes", "Prompt bytes", ".0f"),
    ("telemetry_tool_output_bytes", "Tool output bytes", ".0f"),
    ("telemetry_script_output_bytes", "Script output bytes", ".0f"),
    ("telemetry_retry_count", "Retries", ".0f"),
)

TELEMETRY_METRICS = (
    ("model_invocations", "Invocations", ".0f"),
    ("input_tokens", "Input tokens", ".0f"),
    ("output_tokens", "Output tokens", ".0f"),
    ("prompt_bytes", "Prompt bytes", ".0f"),
    ("tool_output_bytes", "Tool output bytes", ".0f"),
    ("retry_count", "Retries", ".0f"),
)


def median_cell(rows: list[dict], key: str, format_spec: str) -> str:
    numeric = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return format(statistics.median(numeric), format_spec) if numeric else "n/a"


def print_telemetry_table(grouped: dict[str, list[dict]], dimension: str, label: str) -> None:
    observed = False
    table_rows: list[list[str]] = []
    for variant in sorted(grouped):
        runs = grouped[variant]
        names = sorted(
            {
                name
                for run in runs
                for name in run.get("telemetry", {}).get(dimension, {})
            }
        )
        for name in names:
            rows = [
                run["telemetry"][dimension][name]
                for run in runs
                if name in run.get("telemetry", {}).get(dimension, {})
            ]
            if not rows:
                continue
            observed = True
            table_rows.append(
                [
                    variant,
                    name,
                    str(len(rows)),
                    *[median_cell(rows, key, format_spec) for key, _, format_spec in TELEMETRY_METRICS],
                ]
            )

    if not observed:
        return
    print()
    print(f"## Per-{label} telemetry medians")
    print()
    headers = ["Variant", label.title(), "Runs", *[metric_label for _, metric_label, _ in TELEMETRY_METRICS]]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in table_rows:
        print("| " + " | ".join(row) + " |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare medians from collected layered-TDD benchmark runs.")
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()

    grouped: dict[str, list[dict]] = defaultdict(list)
    diagnostic_results = 0
    for path in args.results:
        result = json.loads(path.read_text(encoding="utf-8"))
        metrics = result.setdefault("metrics", {})
        metrics.setdefault(
            "input_tokens_per_run_layer",
            metrics.get("input_tokens_per_expected_layer"),
        )
        metrics.setdefault(
            "cost_per_run_layer_usd",
            metrics.get("cost_per_expected_layer_usd"),
        )
        scope = result.get("benchmark_scope") or {
            "kind": "full",
            "run_layers": [],
            "claim_eligible": True,
        }
        kind = scope.get("kind", "full")
        if kind == "full":
            group_name = result["variant"]
        else:
            diagnostic_results += 1
            layers = ",".join(scope.get("run_layers") or []) or "unspecified"
            group_name = f"{result['variant']} [{kind}:{layers}]"
        grouped[group_name].append(result)

    if diagnostic_results:
        print(
            f"> Note: {diagnostic_results} diagnostic result(s) are grouped separately "
            "and are not eligible for end-to-end token or quality claims."
        )
        print()

    headers = ["Variant", "Runs", *[label for _, label, _ in METRICS], "Oracle"]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for variant in sorted(grouped):
        runs = grouped[variant]
        cells = [variant, str(len(runs))]
        for key, _, format_spec in METRICS:
            cells.append(median_cell([run["metrics"] for run in runs], key, format_spec))
        evaluated = [run.get("evaluation") for run in runs if run.get("evaluation") is not None]
        passed = sum(1 for item in evaluated if item["oracle_passed"] and item["boundary_passed"])
        cells.append(f"{passed}/{len(evaluated)}" if evaluated else "n/a")
        print("| " + " | ".join(cells) + " |")

    print_telemetry_table(grouped, "by_agent", "agent")
    print_telemetry_table(grouped, "by_layer", "layer")


if __name__ == "__main__":
    main()
