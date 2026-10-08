from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
MATERIALIZE = BENCHMARK_ROOT / "scripts" / "materialize"
EVALUATE = BENCHMARK_ROOT / "scripts" / "evaluate.py"
MEASURE = BENCHMARK_ROOT / "scripts" / "measure-reviewer-prompt-growth.py"
RUNNER = BENCHMARK_ROOT / "scripts" / "run-fixed-playbook.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = load_module("measure_reviewer_prompt_growth", MEASURE)
runner = load_module("run_fixed_playbook_funnel", RUNNER)


class PromptGrowthTests(unittest.TestCase):
    def test_detects_raw_stream_growth_and_bounded_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "control.md"
            candidate = root / "candidate.md"
            control.write_text(
                "stdout={{ verification_tests.output.stdout }}\n"
                "stderr={{ verification_tests.output.stderr | replace(\"[\", \"\\\\[\") }}\n",
                encoding="utf-8",
            )
            candidate.write_text(
                "summary={{ verification_summary.output.bounded_excerpt }}\n"
                "evidence={{ verification_summary.output.evidence_path }}\n",
                encoding="utf-8",
            )

            control_result = measure.measure_prompt(control, (10, 1_000))
            candidate_result = measure.measure_prompt(candidate, (10, 1_000))

        self.assertEqual(control_result["raw_stream_references"], 2)
        self.assertEqual(control_result["growth_bytes"], 1_980)
        self.assertEqual(candidate_result["raw_stream_references"], 0)
        self.assertEqual(candidate_result["growth_bytes"], 0)


class DiagnosticScopeTests(unittest.TestCase):
    def test_unexpected_checkpoint_is_recorded_as_non_comparable(self) -> None:
        error = runner.UnexpectedGateError(
            "red_suite_evidence_gate", "agent_test_checkpoint_gate"
        )

        outcome = runner.non_comparable_outcome(
            error, "full", ("L10-domain", "L20-service", "L30-http")
        )

        self.assertEqual(outcome["status"], "non-comparable")
        self.assertFalse(outcome["claim_eligible"])
        self.assertEqual(outcome["reason"], "unexpected-human-gate")
        self.assertEqual(outcome["unexpected_gate"], "agent_test_checkpoint_gate")
        self.assertEqual(outcome["expected_gate"], "red_suite_evidence_gate")

    def test_runner_accepts_only_the_supported_one_layer_scope(self) -> None:
        self.assertEqual(
            runner.benchmark_scope(
                {
                    "run_kind": "one-layer-diagnostic",
                    "run_layers": ["L10-domain"],
                }
            ),
            ("one-layer-diagnostic", ("L10-domain",)),
        )
        with self.assertRaisesRegex(ValueError, "supports only L10-domain"):
            runner.benchmark_scope(
                {
                    "run_kind": "one-layer-diagnostic",
                    "run_layers": ["L20-service"],
                }
            )

    def test_diagnostic_stop_option_does_not_expect_a_feedback_field(self) -> None:
        self.assertEqual(
            runner.layer_approval_response("one-layer-diagnostic", 0, 1),
            (4, ""),
        )
        self.assertEqual(runner.layer_approval_response("full", 0, 3), (1, "feedback"))
        self.assertEqual(runner.layer_approval_response("full", 2, 3), (2, "feedback"))

    def test_materialized_diagnostic_uses_domain_oracle_and_is_not_claim_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            materialized = subprocess.run(
                [
                    str(MATERIALIZE),
                    "--variant",
                    "diagnostic",
                    "--destination",
                    str(project),
                    "--diagnostic-layer",
                    "L10-domain",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(materialized.returncode, 0, materialized.stderr)
            case = json.loads(
                (project / ".benchmark" / "case.json").read_text(encoding="utf-8")
            )
            self.assertEqual(case["run_kind"], "one-layer-diagnostic")
            self.assertEqual(case["run_layers"], ["L10-domain"])
            self.assertFalse(case["claim_eligible"])

            (project / "internal" / "orders" / "order.go").write_text(
                '''package orders

import "errors"

type Status string

const (
    StatusPending Status = "pending"
    StatusFulfilled Status = "fulfilled"
    StatusCancelled Status = "cancelled"
)

var (
    ErrNotFound = errors.New("order not found")
    ErrCannotCancel = errors.New("order cannot be cancelled")
)

type Order struct {
    ID string `json:"id"`
    Status Status `json:"status"`
}

func (o Order) Cancel() (Order, bool, error) {
    if o.Status == StatusCancelled {
        return o, false, nil
    }
    if o.Status != StatusPending {
        return o, false, ErrCannotCancel
    }
    o.Status = StatusCancelled
    return o, true, nil
}
''',
                encoding="utf-8",
            )
            evaluated = subprocess.run(
                [sys.executable, str(EVALUATE), str(project)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(evaluated.returncode, 0, evaluated.stdout + evaluated.stderr)
            result = json.loads(
                (project / ".benchmark" / "evaluation.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["run_kind"], "one-layer-diagnostic")
        self.assertEqual(result["oracle_scope"], "L10-domain")
        self.assertFalse(result["claim_eligible"])
        self.assertTrue(result["oracle_passed"])
        self.assertTrue(result["boundary_passed"])


if __name__ == "__main__":
    unittest.main()
