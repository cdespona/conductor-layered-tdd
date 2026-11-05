"""Drive the canonical workflow with scripted model and human responses."""

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_workflow_optimizations import WORKFLOW_ROOT, working_directory

try:
    from conductor.config.loader import load_workflow
except ModuleNotFoundError as exc:
    if exc.name != "conductor":
        raise
    raise unittest.SkipTest("Run with the Python environment that provides conductor-cli") from exc

from conductor.engine.workflow import WorkflowEngine
from conductor.gates.human import GateResponse
from conductor.providers.copilot import CopilotProvider


class ConductorAcceptanceIntegration(unittest.TestCase):
    def test_pending_order_through_the_real_workflow(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            self.run_fixture()

    def run_fixture(self):
        def write(path, content):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return str(path)

        product = "src/orders.py"
        layer_test = "tests/layer_check.py"
        baseline = "def cancel(order):\n    return order\n"
        write(product, baseline)
        write(".gitignore", "__pycache__/\n.conductor/\n")
        workflow = Path("workflows/conductor/layered-tdd.yaml")
        shutil.copytree(WORKFLOW_ROOT, workflow.parent)
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Smoke", "-c",
                     "user.email=smoke@example.invalid", "commit", "-qm", "baseline")):
            subprocess.run(["git", *args], check=True, capture_output=True)

        plan = Path(".github/plans/smoke")
        todo = plan / "layers/L10.todo.md"
        layer_map = plan / "01-layer-map.md"
        regression = "test ! -f tests/layer_check.py || python3 tests/layer_check.py"
        steps = []

        def model(agent, prompt, context):
            name = agent.name
            steps.append(name)
            if name == "fast_triage":
                return dict(route="standard", reason="New behavior", allowed_paths=[], acceptance_criteria="")
            if name == "requirements_griller":
                path = write(plan / "00-requirements.md", "# Cancel a pending order\n")
                return dict(artifact_path=path, task_slug="smoke", multiple_slices=False,
                            blocker_count=0, summary="One slice")
            if name == "slice_acceptance_planner":
                path = write(plan / "02-slice-acceptance.json", json.dumps(dict(
                    mode="behavior", scenario="Cancelling a pending order persists cancelled status",
                    boundary="File-backed order CLI", design_decisions="JSON fixture and CLI driver",
                    determinism="Fresh temporary order, no clock or network", setup_safety="Seed pending only",
                    unresolved_questions=[], allowed_files=["tests/acceptance.py", "tests/order_cli.py"],
                    protected_files=["tests/acceptance.py", "tests/order_cli.py"], forbidden_files=[product],
                    readiness_command="python3 -c 'compile(open(\"tests/acceptance.py\").read(), \"acceptance\", \"exec\"); compile(open(\"tests/order_cli.py\").read(), \"driver\", \"exec\")'",
                    regression_command=regression, acceptance_tests=[dict(
                        id="test_cancel", scenario="Cancellation persists", file="tests/acceptance.py",
                        command="python3 tests/acceptance.py -v Acceptance.test_cancel",
                        red_result_line="__main__.Acceptance.test_cancel ... FAIL",
                        green_result_line="__main__.Acceptance.test_cancel ... ok",
                        expected_red_exit=1, expected_failure_marker="order should be cancelled",
                    )],
                )))
                return dict(artifact_path=path, summary="Seed pending JSON, invoke CLI and read persisted status")
            if name == "slice_acceptance_builder":
                write("tests/order_cli.py", """import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.orders import cancel

path = Path(sys.argv[1])
path.write_text(json.dumps(cancel(json.loads(path.read_text()))))
""")
                write("tests/acceptance.py", """import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class Acceptance(unittest.TestCase):
    def __str__(self):
        return self.id()

    def test_cancel(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "order.json"
            path.write_text(json.dumps({"status": "pending"}))
            subprocess.run([sys.executable, "tests/order_cli.py", str(path)], check=True)
            self.assertEqual(json.loads(path.read_text())["status"], "cancelled", "order should be cancelled")


if __name__ == "__main__":
    unittest.main()
""")
                return dict(summary="Built CLI driver and persistent black-box acceptance")
            if name == "layer_mapper":
                write(layer_map, "---\nselected_layer: L10\n---\n# Layer map\n")
                write(todo, """---
selected_layer: L10
layer_size: small
work_kind: behavior
status: draft
owner: human
red_gate_state: blocked
---

## Behavior Contract

Cancel a pending order.

## Implementation Boundary

| Allowed Areas | Forbidden Areas | Top-Level Behavior Limits | Read-Only Tests |
| --- | --- | --- | --- |
| `src/orders.py`; `tests/layer_check.py` | `tests/acceptance.py`; `tests/order_cli.py` | Cancel pending only | `tests/layer_check.py` |

## Decision Log

| Decision | Comment | Command/evidence | Timestamp |
| --- | --- | --- | --- |
""")
                return dict(artifact_path=str(layer_map), layer_count=1, recommended_next_layer="L10", summary="One layer")
            if name == "layer_todo_generator":
                return dict(artifact_path=str(todo), selected_layer="L10", test_ownership="human-written",
                            red_gate_state="blocked", ready_for_implementation=False, summary="Layer test ready")
            if name == "implementor":
                write(product, 'def cancel(order):\n    return {**order, "status": "cancelled"}\n')
                return dict(selected_layer="L10", files_modified=[product], checkpoint_required=False,
                            checkpoint_summary="", summary="Implemented cancellation")
            if name == "layer_reviewer":
                return dict(artifact_path=str(todo), approved_recommendation=True, more_layers_remaining=False,
                            recommended_next_layer="", issues=[], summary="One layer complete")
            if name == "final_reviewer":
                path = write(plan / "99-final-review.md", "# Final review\n")
                return dict(artifact_path=path, memory_candidates=[], residual_risks=[],
                            ready_to_finish=True, summary="Acceptance green")
            self.fail(f"Unexpected model call: {name}")

        answers = {
            "graphify_update_gate": ("skip", {}),
            "requirements_gate": ("approve", {"feedback": "One slice"}),
            "slice_acceptance_design_gate": ("approve", {}),
            "slice_acceptance_validation_gate": ("approve", {}),
            "layer_selection_gate": ("selected", {"selected_layer": "L10"}),
            "layer_todo_gate": ("verify_existing_test", {"feedback": "Use layer check"}),
            "red_suite_evidence_gate": ("confirm_red", {"feedback": "Expected cancellation failure"}),
            "layer_approval_gate": ("approve_final_review", {"feedback": "Review slice"}),
            "memory_gate": ("skip", {"feedback": "Smoke fixture"}),
        }

        async def human(prompt):
            steps.append(prompt.name)
            self.assertIn(prompt.name, answers, prompt.prompt)
            value, fields = answers[prompt.name]
            choice = next(choice for choice in prompt.choices if choice.value == value)
            if prompt.name == "slice_acceptance_design_gate":
                self.assertFalse(Path("tests/acceptance.py").exists())
            if prompt.name == "slice_acceptance_validation_gate":
                self.assertEqual(Path(product).read_text(), baseline)
                self.assertIn("tests/acceptance.py", prompt.prompt)
                self.assertIn("tests/order_cli.py", prompt.prompt)
                self.assertEqual(prompt.prompt.count("'exit_code': 1"), 2)
            if prompt.name == "layer_todo_gate":
                write(layer_test, """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.orders import cancel

assert cancel({"status": "pending"})["status"] == "cancelled"
""")
            if prompt.name == "red_suite_evidence_gate":
                self.assertIn("Exit code: 1", prompt.prompt)
            return GateResponse(value, choice.label, fields, prompt.prompt_id)

        async def run():
            engine = WorkflowEngine(load_workflow(workflow), provider=CopilotProvider(mock_handler=model),
                                    workflow_path=workflow.resolve())
            engine.gate_handler.prompt = human
            return await asyncio.wait_for(engine.run(dict(
                request="Cancel a pending order", test_command="python3 -c 'compile(open(\"src/orders.py\").read(), \"orders\", \"exec\")'",
                lint_command="python3 -c 'compile(open(\"src/orders.py\").read(), \"orders\", \"exec\")'", security_command="python3 -c 'pass'",
            )), timeout=45)

        result = asyncio.run(run())
        self.assertEqual(steps, [
            "graphify_update_gate", "fast_triage", "requirements_griller", "requirements_gate",
            "slice_acceptance_planner", "slice_acceptance_design_gate", "slice_acceptance_builder",
            "slice_acceptance_validation_gate", "layer_mapper", "layer_selection_gate",
            "layer_todo_generator", "layer_todo_gate", "red_suite_evidence_gate", "implementor",
            "layer_reviewer", "layer_approval_gate", "final_reviewer", "memory_gate",
        ])
        self.assertEqual(result["status"], "complete")
