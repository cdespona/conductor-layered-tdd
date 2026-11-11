"""Drive layered-star.yaml through the real Conductor engine with fixed answers.

Structural funnel step of the star benchmark: no real model is called. The
mock provider returns fixed agent outputs and a scripted human answers gates.
"""

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_workflow_optimizations import WORKFLOW_ROOT, load_module, working_directory

try:
    from conductor.config.loader import load_workflow
except ModuleNotFoundError as exc:
    if exc.name != "conductor":
        raise
    raise unittest.SkipTest("Run with the Python environment that provides conductor-cli") from exc

from conductor.engine.workflow import WorkflowEngine
from conductor.executor.questions import FREE_TEXT, FREE_TEXT_FIELD
from conductor.gates.human import GateResponse
from conductor.providers.copilot import CopilotProvider

import sys

sys.path.insert(0, str(WORKFLOW_ROOT / "scripts"))
star_ledger = load_module("star_ledger_integration", WORKFLOW_ROOT / "scripts" / "star_ledger.py")

PRODUCT = "src/orders.py"
LAYER_TEST = "tests/layer_check.py"
BASELINE = "def cancel(order):\n    return order\n"
PLAN = Path(".github/plans/smoke")
TODO = PLAN / "layers/L10.todo.md"
LAYER_MAP = PLAN / "01-layer-map.md"
REGRESSION = "test ! -f tests/layer_check.py || python3 tests/layer_check.py"
CHECK = "python3 -c 'compile(open(\"src/orders.py\").read(), \"orders\", \"exec\")'"


def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def contract(outcome="done", reason="", incoherence=None, **fields):
    return dict(fields, outcome=outcome, reason=reason, artifacts=[], incoherence=incoherence or {})


ACCEPTANCE_TEST = """import json
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
"""

DRIVER = """import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.orders import cancel

path = Path(sys.argv[1])
path.write_text(json.dumps(cancel(json.loads(path.read_text()))))
"""

TODO_TEXT = """---
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
"""

LAYER_CHECK = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.orders import cancel

assert cancel({"status": "pending"})["status"] == "cancelled"
"""


class StarFixture:
    """A fresh git repository with the canonical workflow tree copied in."""

    def __init__(self, test, *, incoherence_first=False, unattended_confirm=False, griller_needs_human=False,
                 broken_boundary=False):
        self.test = test
        self.broken_boundary = broken_boundary
        self.griller_needs_human = griller_needs_human
        self.incoherence_first = incoherence_first
        self.unattended_confirm = unattended_confirm
        self.steps = []
        self.implementor_calls = 0
        self.engine = None

    def setup(self):
        write(PRODUCT, BASELINE)
        write(".gitignore", "__pycache__/\n.conductor/\n")
        workflow = Path("workflows/conductor/layered-star.yaml")
        shutil.copytree(WORKFLOW_ROOT, workflow.parent)
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Smoke", "-c",
                     "user.email=smoke@example.invalid", "commit", "-qm", "baseline")):
            subprocess.run(["git", *args], check=True, capture_output=True)
        return workflow

    def model(self, agent, prompt, context):
        name = agent.name
        self.steps.append(name)
        if name == "requirements_griller":
            self.test.assertIn("```text\nCancel a pending order\n```", prompt)
            self.test.assertNotIn("## Star return\nRequest:", prompt)
            path = write(PLAN / "00-requirements.md", "# Cancel a pending order\n")
            outcome = "needs-human" if self.griller_needs_human else "done"
            return contract(outcome=outcome, reason="The requirements artifact is ready for the human gate.",
                            artifact_path=path, task_slug="smoke", multiple_slices=False,
                            blocker_count=0, summary="One slice")
        if name == "fast_triage":
            return contract(route="standard", reason="New behavior", allowed_paths=[], acceptance_criteria="")
        if name == "slice_acceptance_planner":
            path = write(PLAN / "02-slice-acceptance.json", json.dumps(dict(
                mode="behavior", scenario="Cancelling a pending order persists cancelled status",
                boundary="File-backed order CLI", design_decisions="JSON fixture and CLI driver",
                determinism="Fresh temporary order, no clock or network", setup_safety="Seed pending only",
                unresolved_questions=[], allowed_files=["tests/acceptance.py", "tests/order_cli.py"],
                protected_files=["tests/acceptance.py", "tests/order_cli.py"], forbidden_files=[PRODUCT],
                readiness_command="python3 -c 'compile(open(\"tests/acceptance.py\").read(), \"acceptance\", \"exec\"); compile(open(\"tests/order_cli.py\").read(), \"driver\", \"exec\")'",
                regression_command=REGRESSION, acceptance_tests=[dict(
                    id="test_cancel", scenario="Cancellation persists", file="tests/acceptance.py",
                    command="python3 tests/acceptance.py -v Acceptance.test_cancel",
                    red_result_line="__main__.Acceptance.test_cancel ... FAIL",
                    green_result_line="__main__.Acceptance.test_cancel ... ok",
                    expected_red_exit=1, expected_failure_marker="order should be cancelled",
                )],
            )))
            return contract(artifact_path=path, summary="Seed pending JSON, invoke CLI and read persisted status")
        if name == "slice_acceptance_builder":
            write("tests/order_cli.py", DRIVER)
            write("tests/acceptance.py", ACCEPTANCE_TEST)
            return contract(summary="Built CLI driver and persistent black-box acceptance")
        if name == "layer_mapper":
            write(LAYER_MAP, "---\nselected_layer: L10\n---\n# Layer map\n")
            write(TODO, TODO_TEXT.replace("`src/orders.py`; `tests/layer_check.py`", "src/orders.py and its check")
                  if self.broken_boundary else TODO_TEXT)
            return contract(artifact_path=str(LAYER_MAP), layer_count=1, recommended_next_layer="L10", summary="One layer")
        if name == "layer_todo_generator":
            return contract(artifact_path=str(TODO), selected_layer="L10", test_ownership="human-written",
                            red_gate_state="blocked", ready_for_implementation=False, summary="Layer test ready")
        if name == "layer_todo_reviser" and self.broken_boundary:
            self.test.assertIn("- deterministic layer context: Backtick every boundary path", prompt)
            write(TODO, TODO_TEXT)
            return contract(artifact_path=str(TODO), selected_layer="L10", test_ownership="human-written",
                            red_gate_state="blocked", ready_for_implementation=False, summary="Boundary fixed")
        if name == "layer_todo_reviser":
            self.test.assertIn("Hub brief (priority):", prompt)
            self.test.assertIn("second cancellation returns 409", prompt)
            return contract(artifact_path=str(TODO), selected_layer="L10", test_ownership="human-written",
                            red_gate_state="blocked", ready_for_implementation=False, summary="Contract aligned with 409")
        if name == "implementor":
            self.implementor_calls += 1
            self.test.assertIn(agent.model, {"gpt-6-luna", "{{ 'gpt-6-luna' if star_step.output.model_tier == 'small' else 'claude-sonnet-5.5' }}"})
            if self.incoherence_first and self.implementor_calls == 1:
                return contract(outcome="incoherence", reason="Protected HTTP contract test expects 409",
                                incoherence={"claim": "second cancellation returns 200 unchanged",
                                             "source": "layers/L10.todo.md",
                                             "conflicts_with": "tests/http_contract_test: expects 409"},
                                selected_layer="L10", files_modified=[], checkpoint_required=False,
                                checkpoint_summary="", summary="Stopped on contract incoherence")
            write(PRODUCT, 'def cancel(order):\n    return {**order, "status": "cancelled"}\n')
            return contract(selected_layer="L10", files_modified=[PRODUCT], checkpoint_required=False,
                            checkpoint_summary="", summary="Implemented cancellation")
        if name == "layer_reviewer":
            return contract(artifact_path=str(TODO), approved_recommendation=True, more_layers_remaining=False,
                            recommended_next_layer="", issues=[], summary="One layer complete")
        if name == "final_reviewer":
            path = write(PLAN / "99-final-review.md", "# Final review\n")
            return contract(artifact_path=path, memory_candidates=[], residual_risks=[],
                            ready_to_finish=True, summary="Acceptance green")
        if name == "hub":
            hub_input = json.loads(prompt.split("```json", 1)[1].split("```", 1)[0])
            self.test.assertEqual(hub_input["escalation"]["trigger"], "outcome")
            self.test.assertEqual(hub_input["escalation"]["outcome"], "incoherence")
            self.test.assertIn("layer-todo", hub_input["legal_now"])
            for illegal in ("fast-change", "cleanup-implement", "cleanup-verify", "layer-review", "final"):
                self.test.assertNotIn(illegal, hub_input["legal_now"])
            if self.unattended_confirm:
                self.engine.gate_handler.skip_gates = True
            return dict(next="layer-todo", reason="The protected contract test wins; revise the layer contract.",
                        brief="Manda el test de contrato existente: la second cancellation returns 409. Align Gherkin and tests.",
                        alternatives=[{"next": "layer-select", "reason": "Pick another layer first"}],
                        confidence="high")
        self.test.fail(f"Unexpected model call: {name}")

    answers = {
        "graphify_update_gate": ("skip", {}),
        "requirements_gate": ("approve", {"feedback": "One slice"}),
        "slice_acceptance_design_gate": ("approve", {}),
        "slice_acceptance_validation_gate": ("approve", {}),
        "layer_map_gate": ("approve", {"feedback": "One layer"}),
        "layer_selection_gate": ("selected", {"selected_layer": "L10"}),
        "layer_todo_gate": ("verify_existing_test", {"feedback": "Use layer check"}),
        "layer_todo_revision_gate": ("verify_existing_test", {"feedback": "409 contract aligned"}),
        "layer_context_failure_gate": ("revise", {"feedback": "Backtick every boundary path"}),
        "red_suite_evidence_gate": ("confirm_red", {"feedback": "Expected cancellation failure"}),
        "layer_approval_gate": ("approve_final_review", {"feedback": "Review slice"}),
        "memory_gate": ("skip", {"feedback": "Smoke fixture"}),
    }

    async def human(self, prompt):
        self.steps.append(prompt.name)
        if prompt.name == "confirm_gate":
            self.test.assertIn("Proposal: **layer-todo**", prompt.prompt)
            self.test.assertIn("Consequences:", prompt.prompt)
            self.test.assertIn("L[L10].red_gate_allowed", prompt.prompt)
            choice = next(choice for choice in prompt.choices if choice.label == "Accept: layer-todo")
            return GateResponse(choice.value, choice.label, {}, prompt.prompt_id)
        self.test.assertIn(prompt.name, self.answers, prompt.prompt)
        value, fields = self.answers[prompt.name]
        choice = next(choice for choice in prompt.choices if choice.value == value)
        if prompt.name == "slice_acceptance_validation_gate":
            self.test.assertEqual(Path(PRODUCT).read_text(), BASELINE)
        if prompt.name == "layer_todo_gate":
            write(LAYER_TEST, LAYER_CHECK)
        return GateResponse(value, choice.label, fields, prompt.prompt_id)

    async def run(self, workflow):
        self.engine = WorkflowEngine(load_workflow(workflow), provider=CopilotProvider(mock_handler=self.model),
                                     workflow_path=workflow.resolve())
        self.engine.gate_handler.prompt = self.human
        return await asyncio.wait_for(self.engine.run(dict(
            request="Cancel a pending order", test_command=CHECK, lint_command=CHECK,
            security_command="python3 -c 'pass'",
        )), timeout=90)


def ledger_events(path=PLAN / "star-state.json"):
    ledger = star_ledger.Ledger.load(path)
    return ledger, [event["type"] for event in ledger.events]


class ConductorStarIntegration(unittest.TestCase):
    def test_canonical_path_never_calls_the_hub(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            fixture = StarFixture(self)
            result = asyncio.run(fixture.run(fixture.setup()))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(fixture.steps, [
                "graphify_update_gate", "requirements_griller", "requirements_gate", "fast_triage",
                "slice_acceptance_planner", "slice_acceptance_design_gate", "slice_acceptance_builder",
                "slice_acceptance_validation_gate", "layer_mapper", "layer_map_gate", "layer_selection_gate",
                "layer_todo_generator", "layer_todo_gate", "red_suite_evidence_gate", "implementor",
                "layer_reviewer", "layer_approval_gate", "final_reviewer", "memory_gate",
            ])
            ledger, events = ledger_events()
            self.assertNotIn("escalation", events)
            self.assertNotIn("veto", events)
            entered = [e["data"]["spoke"] for e in ledger.events if e["type"] == "spoke_entered"]
            self.assertEqual(entered, [
                "requirements", "triage", "acceptance-design", "acceptance-build", "layer-map", "layer-select",
                "layer-todo", "red-gate", "implement", "verify", "layer-review", "final", "memory",
            ])
            self.assertTrue(all(e["data"]["proposer"] == "router" for e in ledger.events if e["type"] == "spoke_entered"))
            implement = next(e for e in ledger.events if e["type"] == "spoke_entered" and e["data"]["spoke"] == "implement")
            self.assertEqual(implement["data"]["model_tier"], "small")
            self.assertEqual(ledger.snapshot["status"], "done")
            self.assertEqual(ledger.snapshot["layers"]["closed"], ["L10"])
            self.assertFalse(Path(".github/plans/.star-pending/star-state.json").exists())

    def test_needs_human_ready_for_the_spoke_gate_reaches_it_without_the_hub(self):
        """L10 diagnostic regression: a griller answering needs-human must reach requirements_gate."""
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            fixture = StarFixture(self, griller_needs_human=True)
            result = asyncio.run(fixture.run(fixture.setup()))
            self.assertEqual(result["status"], "complete")
            self.assertNotIn("hub", fixture.steps)
            self.assertEqual(fixture.steps[1:4], ["requirements_griller", "requirements_gate", "fast_triage"])
            ledger, events = ledger_events()
            self.assertIn("needs_human_to_gate", events)
            self.assertNotIn("escalation", events)

    def test_failed_layer_context_reaches_its_gate_and_a_revised_todo_recovers(self):
        """L10 regression: an unparseable boundary must never reach the implementor or the hub."""
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            fixture = StarFixture(self, broken_boundary=True)
            result = asyncio.run(fixture.run(fixture.setup()))
            self.assertEqual(result["status"], "complete")
            self.assertNotIn("hub", fixture.steps)
            self.assertEqual(fixture.steps[fixture.steps.index("red_suite_evidence_gate"):][:6], [
                "red_suite_evidence_gate", "layer_context_failure_gate", "layer_todo_reviser",
                "layer_todo_revision_gate", "red_suite_evidence_gate", "implementor",
            ])
            self.assertEqual(fixture.implementor_calls, 1)
            ledger, events = ledger_events()
            failure = next(e for e in ledger.events if e["type"] == "context_failure_to_gate")
            self.assertEqual(failure["data"]["step"], "implementation_context")
            self.assertIn("boundary cell", failure["data"]["reason"])
            self.assertNotIn("escalation", events)

    def test_incoherence_escalates_to_hub_and_confirmed_backtrack_recovers(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            fixture = StarFixture(self, incoherence_first=True)
            result = asyncio.run(fixture.run(fixture.setup()))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(fixture.steps[fixture.steps.index("implementor"):], [
                "implementor", "hub", "confirm_gate", "layer_todo_reviser", "layer_todo_revision_gate",
                "red_suite_evidence_gate", "implementor", "layer_reviewer", "layer_approval_gate",
                "final_reviewer", "memory_gate",
            ])
            ledger, events = ledger_events()
            for kind in ("incoherence_detected", "escalation", "hub_proposal", "confirmation_requested",
                         "jump_confirmed", "facts_staled", "incoherence_resolved", "layer_closed"):
                self.assertIn(kind, events)
            order = [events.index(kind) for kind in ("incoherence_detected", "escalation", "jump_confirmed",
                                                      "incoherence_resolved", "layer_closed")]
            self.assertEqual(order, sorted(order))
            timestamps = {e["type"]: e["ts"] for e in ledger.events}
            self.assertLessEqual(timestamps["incoherence_detected"], timestamps["layer_closed"])
            self.assertEqual(ledger.snapshot["escalations"]["used"], 1)
            self.assertEqual(ledger.snapshot["incoherences"][0]["status"], "resolved")
            reenter = [e for e in ledger.events if e["type"] == "spoke_entered" and e["data"]["spoke"] == "layer-todo"][-1]
            self.assertEqual(reenter["data"]["entry"], "layer_todo_reviser")
            self.assertEqual(reenter["data"]["proposer"], "human")

    def test_unattended_confirmation_stops_with_needs_human(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            fixture = StarFixture(self, incoherence_first=True, unattended_confirm=True)
            with self.assertRaises(Exception) as raised:
                asyncio.run(fixture.run(fixture.setup()))
            self.assertIn("NEEDS_HUMAN", str(raised.exception))
            self.assertEqual(fixture.steps[-2:], ["implementor", "hub"])
            ledger, events = ledger_events()
            self.assertEqual(ledger.snapshot["status"], "needs-human")
            self.assertEqual(ledger.snapshot["pending"]["kind"], "proposal")
            self.assertEqual(events[-1], "run_stopped")
            self.assertEqual(Path(PRODUCT).read_text(), BASELINE)


if __name__ == "__main__":
    unittest.main()
