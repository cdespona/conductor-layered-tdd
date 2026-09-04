from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BENCHMARK_ROOT / "scripts" / "collect-results.py"
RUNNER_PATH = BENCHMARK_ROOT / "scripts" / "run-fixed-playbook.py"
SAMPLE_EVENTS = BENCHMARK_ROOT / "testdata" / "sample-events.jsonl"
SAMPLE_SUMMARY = BENCHMARK_ROOT / "testdata" / "sample-summary.txt"

SPEC = importlib.util.spec_from_file_location("collect_results", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT_PATH}")
collect_results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect_results)

RUNNER_SPEC = importlib.util.spec_from_file_location("run_fixed_playbook", RUNNER_PATH)
if RUNNER_SPEC is None or RUNNER_SPEC.loader is None:
    raise RuntimeError(f"cannot load {RUNNER_PATH}")
run_fixed_playbook = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(run_fixed_playbook)


class EventTelemetryTests(unittest.TestCase):
    def test_parses_per_invocation_layer_and_payload_metrics(self) -> None:
        telemetry = collect_results.parse_event_telemetry(SAMPLE_EVENTS)

        self.assertEqual(telemetry["status"], "available")
        self.assertEqual(telemetry["coverage"], "partial")
        self.assertEqual(telemetry["run_id"], "abc123")
        self.assertEqual(telemetry["conductor_version"], "0.1.26")
        self.assertEqual(telemetry["summary"]["model_invocations"], 3)
        self.assertEqual(telemetry["summary"]["input_tokens"], 300)
        self.assertEqual(telemetry["summary"]["output_tokens"], 30)
        self.assertEqual(telemetry["summary"]["total_tokens"], 330)
        self.assertEqual(telemetry["summary"]["prompt_bytes"], 19)
        self.assertEqual(telemetry["summary"]["tool_calls"], 1)
        self.assertEqual(telemetry["summary"]["tool_argument_bytes"], 15)
        self.assertEqual(telemetry["summary"]["tool_output_bytes"], 13)
        self.assertEqual(telemetry["summary"]["tool_output_truncated_count"], 1)
        self.assertEqual(telemetry["summary"]["script_output_bytes"], 10)
        self.assertEqual(telemetry["summary"]["retry_count"], 1)
        self.assertEqual(telemetry["summary"]["layers_observed"], ["L10-domain"])

        self.assertEqual(telemetry["by_layer"]["L10-domain"]["model_invocations"], 2)
        self.assertEqual(telemetry["by_layer"]["L10-domain"]["input_tokens"], 250)
        self.assertEqual(telemetry["by_layer"]["unscoped"]["input_tokens"], 50)

        first = telemetry["model_invocations"][0]
        self.assertEqual(first["agent"], "layer_todo_generator")
        self.assertEqual(first["layer_id"], "L10-domain")
        self.assertEqual(first["reasoning_effort"], "high")
        self.assertEqual(first["tool_names"], {"view": 1})
        self.assertEqual(first["route_to"], "layer_todo_gate")
        self.assertIsNone(first["tokens"]["cached_input"])
        self.assertFalse(telemetry["availability"]["reasoning_tokens"])
        self.assertFalse(telemetry["availability"]["artifact_bytes"])

        serialized = json.dumps(telemetry)
        self.assertNotIn("prompt-secret", serialized)
        self.assertNotIn("argument-secret", serialized)
        self.assertNotIn("result-secret", serialized)
        self.assertNotIn("feedback-secret", serialized)

    def test_reports_malformed_lines_without_losing_valid_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                SAMPLE_EVENTS.read_text(encoding="utf-8") + "not-json\n",
                encoding="utf-8",
            )
            telemetry = collect_results.parse_event_telemetry(path)

        self.assertEqual(telemetry["status"], "available")
        self.assertEqual(telemetry["malformed_event_lines"], 1)
        self.assertEqual(telemetry["summary"]["model_invocations"], 3)

    def test_reports_missing_event_log_as_unavailable(self) -> None:
        telemetry = collect_results.parse_event_telemetry(None)

        self.assertEqual(telemetry["status"], "unavailable")
        self.assertEqual(telemetry["coverage"], "none")
        self.assertEqual(telemetry["model_invocations"], [])
        self.assertFalse(telemetry["availability"]["input_tokens"])

    def test_focused_fix_route_keeps_the_active_layer(self) -> None:
        events = [
            {
                "type": "workflow_started",
                "timestamp": 1,
                "data": {
                    "agents": [
                        {
                            "name": "implementor",
                            "type": "agent",
                            "model": "gpt-5.4",
                            "provider_name": "copilot",
                            "reasoning_effort": "medium",
                        }
                    ]
                },
            },
            {
                "type": "gate_resolved",
                "timestamp": 2,
                "data": {
                    "agent_name": "layer_selection_gate",
                    "selected_option": "selected",
                    "route": "layer_selection_recorder",
                    "additional_input": {"selected_layer": "L10-domain"},
                },
            },
            {
                "type": "gate_resolved",
                "timestamp": 3,
                "data": {
                    "agent_name": "layer_approval_gate",
                    "selected_option": "request_fixes",
                    "route": "implementor",
                    "additional_input": {"feedback": "fix it"},
                },
            },
            {
                "type": "agent_started",
                "timestamp": 4,
                "data": {
                    "agent_name": "implementor",
                    "iteration": 2,
                    "agent_type": "agent",
                },
            },
            {
                "type": "agent_completed",
                "timestamp": 5,
                "data": {
                    "agent_name": "implementor",
                    "model": "gpt-5.4",
                    "tokens": 11,
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "output": {},
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            telemetry = collect_results.parse_event_telemetry(path)

        self.assertEqual(telemetry["model_invocations"][0]["layer_id"], "L10-domain")

    def test_resolves_adjacent_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_log = Path(directory) / "run.txt"
            adjacent = Path(f"{run_log}.events.jsonl")
            adjacent.write_text("{}\n", encoding="utf-8")

            resolved = collect_results.resolve_events_log(None, run_log, "")

        self.assertEqual(resolved, adjacent)


class CollectResultsCliTests(unittest.TestCase):
    def test_cli_embeds_telemetry_and_reconciles_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            benchmark = project / ".benchmark"
            benchmark.mkdir(parents=True)
            (benchmark / "case.json").write_text(
                json.dumps(
                    {
                        "case": "idempotent-cancellation",
                        "expected_layers": 3,
                        "run_kind": "one-layer-diagnostic",
                        "run_layers": ["L10-domain"],
                        "claim_eligible": False,
                        "test_command": "make test",
                        "graphify": False,
                    }
                ),
                encoding="utf-8",
            )
            run_log = root / "run.txt"
            run_log.write_text(SAMPLE_SUMMARY.read_text(encoding="utf-8"), encoding="utf-8")
            events_log = Path(f"{run_log}.events.jsonl")
            events_log.write_text(SAMPLE_EVENTS.read_text(encoding="utf-8"), encoding="utf-8")
            output = root / "result.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--variant",
                    "telemetry",
                    "--run-log",
                    str(run_log),
                    "--project",
                    str(project),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result["metrics"]["model_invocations"], 3)
        self.assertEqual(result["metrics"]["input_tokens_per_run_layer"], 1_051_123)
        self.assertEqual(result["benchmark_scope"]["kind"], "one-layer-diagnostic")
        self.assertEqual(result["benchmark_scope"]["run_layers"], ["L10-domain"])
        self.assertFalse(result["benchmark_scope"]["claim_eligible"])
        self.assertEqual(result["metrics"]["summary_cost_rows"], 6)
        self.assertEqual(result["metrics"]["telemetry_input_tokens"], 300)
        self.assertEqual(result["telemetry"]["reconciliation"]["event_input_tokens"], 300)
        self.assertEqual(
            result["telemetry"]["reconciliation"]["input_token_difference"],
            1_050_823,
        )

    def test_comparator_prints_agent_and_layer_telemetry(self) -> None:
        compare_script = BENCHMARK_ROOT / "scripts" / "compare-results.py"
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "variant": "telemetry",
                        "benchmark_scope": {
                            "kind": "one-layer-diagnostic",
                            "run_layers": ["L10-domain"],
                            "claim_eligible": False,
                        },
                        "metrics": {
                            "input_tokens": 300,
                            "output_tokens": 30,
                            "total_cost_usd": 0.035,
                            "model_invocations": 3,
                            "input_tokens_per_invocation": 100,
                            "cost_per_expected_layer_usd": 0.0117,
                            "telemetry_prompt_bytes": 19,
                            "telemetry_tool_output_bytes": 13,
                            "telemetry_script_output_bytes": 10,
                            "telemetry_retry_count": 1,
                        },
                        "telemetry": {
                            "by_agent": {
                                "layer_reviewer": {
                                    "model_invocations": 1,
                                    "input_tokens": 150,
                                    "output_tokens": 15,
                                    "prompt_bytes": 6,
                                    "tool_output_bytes": 0,
                                    "retry_count": 1,
                                }
                            },
                            "by_layer": {
                                "L10-domain": {
                                    "model_invocations": 2,
                                    "input_tokens": 250,
                                    "output_tokens": 25,
                                    "prompt_bytes": 19,
                                    "tool_output_bytes": 13,
                                    "retry_count": 1,
                                }
                            },
                        },
                        "evaluation": {"oracle_passed": True, "boundary_passed": True},
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(compare_script), str(result_path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("not eligible for end-to-end", completed.stdout)
        self.assertIn("telemetry [one-layer-diagnostic:L10-domain]", completed.stdout)
        self.assertIn("## Per-agent telemetry medians", completed.stdout)
        self.assertIn("layer_reviewer", completed.stdout)
        self.assertIn("## Per-layer telemetry medians", completed.stdout)
        self.assertIn("L10-domain", completed.stdout)


class FixedPlaybookTelemetryTests(unittest.TestCase):
    def test_preserves_runtime_event_log_beside_run_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "conductor"
            source_dir.mkdir()
            source = source_dir / "conductor-layered-tdd-20260804-120000-deadbeef.events.jsonl"
            source.write_text('{"type":"workflow_started"}\n', encoding="utf-8")
            destination = root / "run.txt.events.jsonl"

            preserved = run_fixed_playbook.preserve_event_log(
                "deadbeef",
                destination,
                source_dir,
            )

            self.assertEqual(preserved, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
