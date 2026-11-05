from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workflow_optimizations import (
    WORKFLOW, SCRIPTS, load_module, working_directory,
)

acceptance = load_module("slice_acceptance", SCRIPTS / "slice-acceptance.py")


class SliceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cwd = working_directory(self.root)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        self.git("init", "-q")
        (self.root / ".gitignore").write_text("__pycache__/\n")
        (self.root / "product.py").write_text("print('pending')\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline")
        self.plan_path = Path(".github/plans/slice/02-slice-acceptance.json")
        self.plan_path.parent.mkdir(parents=True)
        self.plan = {
            "mode": "behavior", "scenario": "Given a pending order, when cancelled, then its status is cancelled",
            "boundary": "product CLI", "design_decisions": "CLI driver and pending order setup",
            "determinism": "fresh directory, no clock or network", "setup_safety": "initial state only",
            "unresolved_questions": [], "allowed_files": ["at.py", "order.json"],
            "protected_files": ["at.py", "order.json"], "forbidden_files": ["product.py"],
            "readiness_command": "python3 -c \"from pathlib import Path; import json; compile(Path('at.py').read_text(), 'at.py', 'exec'); assert json.loads(Path('order.json').read_text())['status'] == 'pending'\"",
            "regression_command": "python3 -c \"from pathlib import Path; compile(Path('product.py').read_text(), 'product.py', 'exec')\"",
            "acceptance_tests": [{
                "id": "test_cancel", "scenario": "Cancellation persists", "file": "at.py",
                "command": "python3 -m unittest -v at.Acceptance.test_cancel",
                "red_result_line": "at.Acceptance.test_cancel ... FAIL",
                "green_result_line": "at.Acceptance.test_cancel ... ok",
                "expected_red_exit": 1, "expected_failure_marker": "order status should be cancelled",
            }],
        }
        self.write_plan()

    def git(self, *args):
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout

    def write_plan(self):
        self.plan_path.write_text(json.dumps(self.plan))

    def prepare(self):
        result = acceptance.prepare(self.plan_path)
        self.state_path = Path(result["state_path"])
        self.addCleanup(self.state_path.unlink, missing_ok=True)
        return result

    def build(self):
        Path("order.json").write_text('{"status":"pending"}\n')
        Path("at.py").write_text(
            "import json, subprocess, sys, unittest\n"
            "from pathlib import Path\n"
            "class Acceptance(unittest.TestCase):\n"
            "    def __str__(self): return self.id()\n"
            "    def test_cancel(self):\n"
            "        self.assertEqual(json.loads(Path('order.json').read_text())['status'], 'pending')\n"
            "        observed = subprocess.check_output([sys.executable, 'product.py'], text=True).strip()\n"
            "        self.assertEqual(observed, 'cancelled', 'order status should be cancelled')\n"
        )

    def approve(self):
        self.prepare()
        self.build()
        self.assertTrue(acceptance.execute("verify", self.state_path)["proceed"])
        self.assertTrue(acceptance.execute("approve", self.state_path)["proceed"])

    def test_complete_outer_cycle_keeps_product_for_layers(self):
        self.approve()
        self.assertEqual(Path("product.py").read_text(), "print('pending')\n")
        self.assertTrue(acceptance.execute("check", self.state_path)["proceed"])
        self.assertFalse(acceptance.execute("final", self.state_path)["proceed"])
        Path("product.py").write_text("print('cancelled')\n")
        self.assertTrue(acceptance.execute("final", self.state_path)["proceed"])

    def test_human_validation_cannot_precede_support_verification(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "verification must pass"):
            acceptance.execute("approve", self.state_path)
        with self.assertRaisesRegex(ValueError, "human validation"):
            acceptance.execute("check", self.state_path)

    def test_unknown_modeling_decisions_block_prepare(self):
        self.plan["unresolved_questions"] = ["Which persistence representation?"]
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "resolve all"):
            self.prepare()

    def test_explicitly_approved_persistence_setup_is_allowed(self):
        self.plan["allowed_files"].append("model.py")
        self.plan["protected_files"].append("model.py")
        self.write_plan()
        self.prepare()
        self.build()
        Path("model.py").write_text("PENDING = 'pending'\n")
        self.assertTrue(acceptance.execute("verify", self.state_path)["proceed"])

    def test_unapproved_production_edit_is_blocked(self):
        self.prepare()
        self.build()
        Path("product.py").write_text("print('cancelled')\n")
        with self.assertRaisesRegex(ValueError, "boundary violations: product.py"):
            acceptance.execute("verify", self.state_path)

    def test_modified_plan_is_not_implicit_approval(self):
        self.prepare()
        self.build()
        self.plan["allowed_files"].append("product.py")
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "plan was modified"):
            acceptance.execute("verify", self.state_path)

    def test_missing_driver_is_blocker_not_meaningful_red(self):
        self.prepare()
        self.build()
        Path("at.py").unlink()
        with self.assertRaises(OSError):
            acceptance.execute("verify", self.state_path)

    def test_compile_failure_blocks_even_with_expected_assertion_in_source(self):
        self.prepare()
        self.build()
        Path("at.py").write_text("syntax error # order status should be cancelled\n")
        result = acceptance.execute("verify", self.state_path)
        self.assertFalse(result["proceed"])
        self.assertIn("readiness_command failed", result["summary"])
        with self.assertRaises(ValueError):
            acceptance.execute("approve", self.state_path)

    def test_regression_failure_is_never_classified_as_outer_red(self):
        self.plan["regression_command"] = "python3 -c 'raise AssertionError(\"regression\")'"
        self.write_plan()
        self.prepare()
        self.build()
        result = acceptance.execute("verify", self.state_path)
        self.assertFalse(result["proceed"])
        self.assertIn("regression_command failed", result["summary"])
        self.assertEqual(len(result["evidence"]), 2)

    def test_wrong_acceptance_failure_is_blocked(self):
        self.prepare()
        self.build()
        Path("at.py").write_text("raise RuntimeError('driver unavailable')\n")
        self.assertFalse(acceptance.execute("verify", self.state_path)["proceed"])

    def test_both_red_runs_must_match_approved_failure(self):
        self.prepare()
        self.build()
        real_run = acceptance.verification.run
        calls = 0
        def run(*args):
            nonlocal calls
            calls += 1
            result = real_run(*args)
            if calls == 4:
                result["exit_code"] = 0
            return result
        with patch.object(acceptance.verification, "run", side_effect=run):
            self.assertFalse(acceptance.execute("verify", self.state_path)["proceed"])
        self.assertEqual(calls, 4)

    def test_missing_inventory_requires_design_reapproval(self):
        self.plan.pop("acceptance_tests")
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "declare acceptance_tests"):
            self.prepare()

    def test_every_declared_test_must_be_executed(self):
        second = dict(self.plan["acceptance_tests"][0], id="test_read", scenario="Fresh status",
                      command="python3 -m unittest -v at.Acceptance.test_read",
                      red_result_line="at.Acceptance.test_read ... FAIL",
                      green_result_line="at.Acceptance.test_read ... ok")
        self.plan["acceptance_tests"].append(second)
        self.write_plan()
        self.prepare()
        self.build()
        result = acceptance.execute("verify", self.state_path)
        self.assertFalse(result["proceed"])
        self.assertIn("test_read", result["summary"])
        self.assertEqual([entry["test_id"] for entry in result["evidence"][2:]],
                         ["test_cancel", "test_cancel", "test_read"])

    def test_success_without_the_expected_test_result_cannot_close(self):
        self.approve()
        real_run = acceptance.verification.run

        def run(command, kind, limit):
            if kind == "slice-acceptance":
                command = "python3 -c 'pass'"
            return real_run(command, kind, limit)

        with patch.object(acceptance.verification, "run", side_effect=run):
            result = acceptance.execute("final", self.state_path)
        self.assertFalse(result["proceed"])
        self.assertIn("test_cancel", result["summary"])

    def test_unchanged_gherkin_does_not_allow_changed_dsl(self):
        self.approve()
        Path("at.py").write_text("print('fake pass')\n")
        with self.assertRaisesRegex(ValueError, "support changed"):
            acceptance.execute("check", self.state_path)
        with self.assertRaises(ValueError):
            acceptance.execute("final", self.state_path)

    def test_edit_between_verification_and_human_approval_is_blocked(self):
        self.prepare()
        self.build()
        self.assertTrue(acceptance.execute("verify", self.state_path)["proceed"])
        Path("order.json").write_text('{"status":"cancelled"}\n')
        with self.assertRaisesRegex(ValueError, "changed after verification"):
            acceptance.execute("approve", self.state_path)

    def test_source_edit_after_verification_invalidates_actual_change_review(self):
        self.prepare()
        self.build()
        self.assertTrue(acceptance.execute("verify", self.state_path)["proceed"])
        Path("product.py").write_text("print('cancelled')\n")
        with self.assertRaisesRegex(ValueError, "changed after verification"):
            acceptance.execute("approve", self.state_path)

    def test_verification_command_cannot_sneak_in_product_implementation(self):
        self.plan["regression_command"] = "python3 -c \"from pathlib import Path; Path('extra.py').write_text('injected')\""
        self.write_plan()
        self.prepare()
        self.build()
        result = acceptance.execute("verify", self.state_path)
        self.assertFalse(result["proceed"])
        self.assertIn("changed repository sources", result["summary"])

    def test_plan_change_after_approval_requires_human_review(self):
        self.approve()
        self.plan["scenario"] = "weakened scenario"
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "human design approval"):
            acceptance.execute("check", self.state_path)

    def test_preservation_mode_requires_reason_and_green_checks(self):
        self.plan.update(mode="preservation", allowed_files=[], protected_files=[],
                         not_applicable_reason="Remove obsolete private helper; existing behavior stays unchanged",
                         readiness_command=self.plan["regression_command"])
        self.write_plan()
        self.prepare()
        self.assertTrue(acceptance.execute("verify", self.state_path)["proceed"])
        acceptance.execute("approve", self.state_path)
        self.assertTrue(acceptance.execute("final", self.state_path)["proceed"])

    def test_preservation_cannot_silently_skip_acceptance(self):
        self.plan["mode"] = "preservation"
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "justification"):
            self.prepare()

    def test_scope_rejects_globs_parent_traversal_and_symlink_escapes(self):
        for value in ["../escape", "tests/*", "/tmp/test", "workflows/conductor/x.py", "AGENTS.md"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                acceptance.paths([value])
        Path("escape").symlink_to("/tmp")
        with self.assertRaises(ValueError):
            acceptance.paths(["escape/test.py"])

    def test_symlink_created_during_support_does_not_expand_scope(self):
        self.prepare()
        self.build()
        Path("order.json").unlink()
        Path("order.json").symlink_to("product.py")
        with self.assertRaisesRegex(ValueError, "invalid acceptance file scope"):
            acceptance.execute("verify", self.state_path)


class SliceAcceptanceRoutingTests(unittest.TestCase):
    @staticmethod
    def blocks():
        content = WORKFLOW.read_text()
        return {part.splitlines()[0]: part for part in content.split("  - name: ")[1:]}

    def test_all_standard_requirement_approvals_prepare_acceptance_first(self):
        blocks = self.blocks()
        for name in ("requirements_gate", "requirements_revision_gate", "slice_requirements_gate", "slice_requirements_revision_gate"):
            self.assertIn("route: slice_acceptance_planner", blocks[name])
            self.assertNotIn("route: layer_mapper", blocks[name])
        self.assertIn("route: slice_acceptance_prepare", blocks["slice_acceptance_design_gate"])
        self.assertIn("to: slice_acceptance_builder", blocks["slice_acceptance_prepare"])
        self.assertIn("to: slice_acceptance_verify", blocks["slice_acceptance_builder"])
        self.assertIn("to: slice_acceptance_validation_gate", blocks["slice_acceptance_verify"])
        self.assertIn("route: slice_acceptance_approve", blocks["slice_acceptance_validation_gate"])
        self.assertIn("to: layer_mapper", blocks["slice_acceptance_approve"])

    def test_no_other_route_enters_mapping_or_final_review_without_verification(self):
        incoming_map = []; incoming_final = []
        for name, block in self.blocks().items():
            targets = re.findall(r"(?:to|route): ([a-z_]+)", block)
            if "layer_mapper" in targets: incoming_map.append(name)
            if "final_reviewer" in targets: incoming_final.append(name)
        self.assertEqual(incoming_map, ["slice_acceptance_approve"])
        self.assertEqual(incoming_final, ["slice_acceptance_final"])
        self.assertIn("when: \"{{ output.proceed }}\"", self.blocks()["slice_acceptance_final"])

    def test_guards_cover_behavior_cleanup_and_review_paths(self):
        blocks = self.blocks()
        for source, target in (("implementation_context", "layer_snapshot"),
                               ("cleanup_implementation_context", "cleanup_layer_snapshot"),
                               ("layer_diff", "layer_reviewer"),
                               ("cleanup_layer_diff", "cleanup_reviewer")):
            self.assertIn("to: acceptance_guard_" + target, blocks[source])
            self.assertIn("- check", blocks["acceptance_guard_" + target])
        for name in ("red_suite_verifier", "verification_tests", "cleanup_baseline_tests", "cleanup_verification_tests"):
            self.assertIn("slice_acceptance_prepare.output.regression_command", blocks[name])
            self.assertNotIn("workflow.input.test_command", blocks[name])


if __name__ == "__main__":
    unittest.main()
