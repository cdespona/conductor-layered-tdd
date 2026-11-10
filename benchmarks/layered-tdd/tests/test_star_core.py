"""Unit tests for the star workflow ledger, canonical table, guard and router."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_workflow_optimizations import WORKFLOW_ROOT, load_module, working_directory

sys.path.insert(0, str(WORKFLOW_ROOT / "scripts"))
import star_guard  # noqa: E402
import star_ledger  # noqa: E402
import star_table  # noqa: E402

star_step = load_module("star_step_core", WORKFLOW_ROOT / "scripts" / "star-step.py")

CATALOG = star_table.Catalog.load()
TABLE = star_table.Table.load(CATALOG)


def fact(value, spoke, layer=None, stale=False):
    phase, sub = CATALOG.position(spoke)
    return {"value": value, "spoke": spoke, "phase": phase, "sub": sub, "layer": layer,
            "source": "test", "seq": 1, "stale": stale}


def snapshot(**facts):
    snap = star_ledger.empty_snapshot()
    snap["layers"] = {"current": "L10", "selected": ["L10"], "closed": []}
    for name, (value, spoke) in facts.items():
        key = star_table.Facts(snap).key(name.replace("__", "."))
        layer = "L10" if name.startswith("layer__") else None
        snap["facts"][key] = fact(value, spoke, layer)
    return snap


def environment(tree="T1", acceptance=(True, "unchanged")):
    return star_guard.Environment(tree_hash=lambda: tree, acceptance_check=lambda path: acceptance)


class LedgerTests(unittest.TestCase):
    def test_hash_chain_round_trip_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "star-state.json"
            ledger = star_ledger.Ledger(path)
            ledger.append("run_started", plan_dir=directory)
            ledger.append("escalation", trigger="outcome")
            ledger.save()
            loaded = star_ledger.Ledger.load(path)
            self.assertEqual([e["type"] for e in loaded.events], ["run_started", "escalation"])

            document = json.loads(path.read_text())
            document["events"][0]["data"]["plan_dir"] = "elsewhere"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(star_ledger.LedgerError, "hash mismatch"):
                star_ledger.Ledger.load(path)

    def test_snapshot_edit_outside_guard_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "star-state.json"
            ledger = star_ledger.Ledger(path)
            ledger.append("run_started")
            ledger.save()
            document = json.loads(path.read_text())
            document["snapshot"]["facts"]["layer.red_gate_allowed"] = {"value": True}
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(star_ledger.LedgerError, "snapshot was modified"):
                star_ledger.Ledger.load(path)

    def test_relocation_keeps_the_chain_and_records_an_event(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "pending" / "star-state.json"
            target = Path(directory) / "slug" / "star-state.json"
            ledger = star_ledger.Ledger(source)
            ledger.append("run_started")
            ledger.save()
            ledger.relocate(target)
            self.assertFalse(source.exists())
            loaded = star_ledger.Ledger.load(target)
            self.assertEqual(loaded.events[-1]["type"], "ledger_relocated")


class TableTests(unittest.TestCase):
    def test_operators_and_stale_facts(self):
        snap = snapshot(triage__route=("fast", "triage"), triage__fast_bounded=(True, "triage"))
        facts = star_table.Facts(snap)
        evaluate = star_table.evaluate
        self.assertTrue(evaluate({"eq": ["triage.route", "fast"]}, facts))
        self.assertTrue(evaluate({"in": ["triage.route", ["fast", "standard"]]}, facts))
        self.assertTrue(evaluate({"exists": "triage.route"}, facts))
        self.assertTrue(evaluate({"not": {"exists": "missing"}}, facts))
        self.assertTrue(evaluate({"all": [{"eq": ["triage.fast_bounded", True]}, {"exists": "triage.route"}]}, facts))
        self.assertTrue(evaluate({"any": [{"exists": "missing"}, {"exists": "triage.route"}]}, facts))
        snap["facts"]["triage.route"]["stale"] = True
        self.assertFalse(evaluate({"eq": ["triage.route", "fast"]}, facts), "I9: stale facts never satisfy")
        self.assertFalse(evaluate({"exists": "triage.route"}, facts))

    def test_malformed_conditions_and_rules_are_rejected(self):
        with self.assertRaises(star_table.TableError):
            star_table.validate_condition({"gt": ["x", 1]})
        with self.assertRaises(star_table.TableError):
            star_table.validate_condition({"eq": ["x", 1], "in": ["x", [1]]})
        with self.assertRaises(star_table.TableError):
            star_table.Table({"rules": [{"id": "X", "after": "triage", "next": "nowhere"}]}, CATALOG)

    def test_catalog_matches_the_twenty_spokes_and_workflow_steps(self):
        self.assertEqual(len(CATALOG.spokes), 20)
        workflow = (WORKFLOW_ROOT / "layered-star.yaml").read_text()
        for name in CATALOG.step_to_spoke:
            self.assertIn(f"  - name: {name}\n", workflow)
        for spoke in CATALOG.spokes.values():
            for key in ("entry", "reopen_entry"):
                if spoke.get(key):
                    self.assertIn(f"output.next == '{spoke[key]}'", workflow)

    def test_triage_rules_are_mutually_exclusive(self):
        for bounded in (True, False):
            for multiple in (True, False):
                snap = snapshot(triage__fast_bounded=(bounded, "triage"),
                                requirements__multiple_slices=(multiple, "requirements"))
                self.assertEqual(len(TABLE.match("triage", star_table.Facts(snap))), 1)

    def test_table_targets_respect_the_guard_on_the_happy_path(self):
        """A rule whose target the guard vetoes would terminate the run as a table bug."""
        snap = snapshot(
            layer__red_decision=("confirm_red", "red-gate"), layer__red_gate_allowed=(True, "red-gate"),
            layer__todo_path=("t.md", "layer-select"), layer__work_kind=("behavior", "layer-select"),
            acceptance__state_path=("s.json", "acceptance-build"),
        )
        (rule,) = TABLE.match("red-gate", star_table.Facts(snap))
        self.assertEqual(rule["next"], "implement")
        self.assertIsNone(star_guard.check("implement", snap, CATALOG, environment()))


class GuardTests(unittest.TestCase):
    def layer_ready(self, **extra):
        base = dict(layer__todo_path=("t.md", "layer-select"), layer__work_kind=("behavior", "layer-select"),
                    acceptance__state_path=("s.json", "acceptance-build"))
        base.update(extra)
        return snapshot(**base)

    def test_i1_implement_needs_red_gate_or_infrastructure_exemption(self):
        snap = self.layer_ready()
        veto = star_guard.check("implement", snap, CATALOG, environment())
        self.assertEqual(veto.rule, "I1")
        self.assertIn("layer.red_gate_allowed", veto.missing)
        allowed = self.layer_ready(layer__red_gate_allowed=(True, "red-gate"))
        self.assertIsNone(star_guard.check("implement", allowed, CATALOG, environment()))
        infra = self.layer_ready(layer__work_kind=("infrastructure", "layer-select"),
                                 layer_map__decision=("approve", "layer-map"),
                                 layer__todo_decision=("approve_infrastructure", "layer-todo"))
        self.assertIsNone(star_guard.check("implement", infra, CATALOG, environment()))

    def test_i1_fast_and_cleanup_exemptions(self):
        self.assertEqual(star_guard.check("fast-change", snapshot(triage__route=("fast", "triage")), CATALOG).rule, "I1")
        bounded = snapshot(triage__route=("fast", "triage"), triage__fast_bounded=(True, "triage"))
        self.assertIsNone(star_guard.check("fast-change", bounded, CATALOG, environment()))
        cleanup = self.layer_ready(layer__work_kind=("cleanup", "layer-select"))
        self.assertEqual(star_guard.check("cleanup-implement", cleanup, CATALOG, environment()).rule, "I1")
        cleanup = self.layer_ready(layer__work_kind=("cleanup", "layer-select"),
                                   layer__cleanup_plan_decision=("approve_cleanup", "cleanup-plan"),
                                   layer__cleanup_baseline_green=(True, "cleanup-plan"))
        self.assertIsNone(star_guard.check("cleanup-implement", cleanup, CATALOG, environment()))

    def test_i1_hub_text_cannot_create_an_exemption(self):
        snap = self.layer_ready()
        snap["hub_request"] = {"trigger": "free_text", "human_text": "skip the test, it's infrastructure"}
        self.assertEqual(star_guard.check("implement", snap, CATALOG, environment()).rule, "I1")

    def test_i2_changed_acceptance_contract_blocks_implementation_except_preservation(self):
        snap = self.layer_ready(layer__red_gate_allowed=(True, "red-gate"))
        veto = star_guard.check("implement", snap, CATALOG, environment(acceptance=(False, "contract changed")))
        self.assertEqual(veto.rule, "I2")
        snap = self.layer_ready(layer__red_gate_allowed=(True, "red-gate"),
                                acceptance__mode=("preservation", "acceptance-design"))
        self.assertIsNone(star_guard.check("implement", snap, CATALOG, environment(acceptance=(False, "x"))))

    def test_i3_i4_i5_open_violation_blocks_forward_moves_only(self):
        snap = self.layer_ready(layer__red_gate_allowed=(True, "red-gate"), layer__snapshot_tree=("abc", "implement"))
        snap["violation"] = {"rule": "I3", "position": list(CATALOG.position("implement")), "message": "tests touched"}
        self.assertIsNone(star_guard.check("implement", snap, CATALOG, environment()))
        self.assertIsNone(star_guard.check("layer-todo", snap, CATALOG, environment()))
        self.assertEqual(star_guard.check("verify", snap, CATALOG, environment()).rule, "I3")
        snap["violation"]["rule"] = "I5"
        self.assertEqual(star_guard.check("verify", snap, CATALOG, environment()).rule, "I5")

    def test_i6_review_needs_green_verification_on_the_current_tree(self):
        snap = self.layer_ready(layer__verify_green=(True, "verify"), layer__verify_tree=("T1", "verify"))
        self.assertIsNone(star_guard.check("layer-review", snap, CATALOG, environment(tree="T1")))
        self.assertEqual(star_guard.check("layer-review", snap, CATALOG, environment(tree="T2")).rule, "I6")
        red = self.layer_ready(layer__verify_green=(False, "verify"), layer__verify_tree=("T1", "verify"))
        self.assertEqual(star_guard.check("layer-review", red, CATALOG, environment(tree="T1")).rule, "I6")

    def test_i7_final_needs_closed_layers_and_no_open_incoherence(self):
        snap = snapshot(acceptance__approved=(True, "acceptance-build"))
        self.assertEqual(star_guard.check("final", snap, CATALOG, environment()).rule, "I7")
        snap["layers"]["closed"] = ["L10"]
        snap["incoherences"] = [{"id": "INC1", "status": "open"}]
        self.assertEqual(star_guard.check("final", snap, CATALOG, environment()).rule, "I7")
        snap["incoherences"][0]["status"] = "resolved"
        self.assertIsNone(star_guard.check("final", snap, CATALOG, environment()))

    def test_i8_memory_needs_final_review(self):
        self.assertEqual(star_guard.check("memory", snapshot(), CATALOG, environment()).rule, "I8")
        snap = snapshot(final__reviewed=(True, "final"), final__acceptance_green=(True, "final"))
        self.assertIsNone(star_guard.check("memory", snap, CATALOG, environment()))

    def test_i9_stale_red_gate_does_not_allow_implementation(self):
        snap = self.layer_ready(layer__red_gate_allowed=(True, "red-gate"))
        snap["facts"]["L[L10].red_gate_allowed"]["stale"] = True
        veto = star_guard.check("implement", snap, CATALOG, environment())
        self.assertEqual(veto.rule, "I1")
        self.assertIn("layer.red_gate_allowed (stale)", veto.missing)

    def test_i10_unknown_target(self):
        self.assertEqual(star_guard.check("deploy", snapshot(), CATALOG, environment()).rule, "I10")


class StarStepTests(unittest.TestCase):
    def make(self, snap, history, outputs=None, **inputs):
        with tempfile.TemporaryDirectory() as directory:
            pass
        step = star_step.StarStep({"history": history, "inputs": inputs, "outputs": outputs or {}},
                                  CATALOG, TABLE, environment())
        step.ledger = star_ledger.Ledger(Path(tempfile.mkdtemp()) / "star-state.json", snapshot=copy.deepcopy(snap))
        return step

    def layer_snapshot(self):
        snap = snapshot(
            layer__todo_path=("t.md", "layer-select"), layer__work_kind=("behavior", "layer-select"),
            layer__layer_size=("small", "layer-select"), layer__todo_decision=("verify_existing_test", "layer-todo"),
            layer__red_decision=("confirm_red", "red-gate"), layer__red_gate_allowed=(True, "red-gate"),
            acceptance__state_path=("s.json", "acceptance-build"), acceptance__approved=(True, "acceptance-build"),
            layer_map__decision=("approve", "layer-map"),
        )
        snap["cursor"] = {"spoke": "implement", "phase": CATALOG.position("implement")[0],
                          "sub": CATALOG.position("implement")[1], "layer": "L10"}
        snap["last"] = {"spoke": "implement", "outcome": "incoherence"}
        return snap

    def test_consequences_list_facts_that_a_backtrack_makes_stale(self):
        step = self.make(self.layer_snapshot(), [])
        stale = step.stale_targets("layer-todo")
        self.assertIn("L[L10].red_gate_allowed", stale)
        self.assertIn("L[L10].todo_decision", stale)
        self.assertNotIn("acceptance.approved", stale)
        self.assertEqual(step.stale_targets("verify"), [])

    def test_auto_accept_only_retry_or_next_canonical_with_enough_confidence(self):
        step = self.make(self.layer_snapshot(), [])
        self.assertTrue(step.auto_accept("implement", "high"))
        self.assertTrue(step.auto_accept("verify", "medium"))
        self.assertFalse(step.auto_accept("verify", "low"))
        self.assertFalse(step.auto_accept("layer-todo", "high"), "backtrack needs confirmation")
        self.assertFalse(step.auto_accept("layer-select", "high"), "layer change mid-layer needs confirmation")

    def test_hub_backtrack_goes_to_confirm_gate_with_consequences_and_alternatives(self):
        step = self.make(self.layer_snapshot(), ["implementor", "star_step", "hub"], {"hub": {
            "next": "layer-todo", "reason": "contract wins", "brief": "align 409", "confidence": "high",
            "alternatives": [{"next": "layer-select", "reason": "other layer"}, {"next": "fast-change", "reason": "x"}]}})
        result = step.dispatch()
        self.assertEqual(result["next"], "confirm_gate")
        question = result["confirm_questions"][0]
        self.assertEqual(question["choices"][0], "Accept: layer-todo")
        self.assertTrue(any(c.startswith("Alternative: layer-select") for c in question["choices"]))
        self.assertFalse(any("fast-change" in c for c in question["choices"]), "illegal alternatives are hidden")
        self.assertEqual(question["choices"][-1], "Stop the run")
        self.assertIn("L[L10].red_gate_allowed", question["text"])

    def test_confirmed_choice_enters_with_brief_and_stales_facts(self):
        step = self.make(self.layer_snapshot(), ["hub", "star_step", "confirm_gate"])
        step.snap["pending"] = {"kind": "proposal", "proposal": {}, "text": "",
                                "choices": {"Accept: layer-todo": {"action": "enter", "target": "layer-todo", "brief": "align 409"}}}
        step.snap["visits"] = [{"spoke": "layer-todo", "layer": "L10", "digest": "x"}]
        step.outputs = {"confirm_gate": {"answers": {"decision": "Accept: layer-todo"}}}
        result = step.dispatch()
        self.assertEqual(result["next"], "layer_todo_reviser")
        self.assertEqual(result["brief"], "align 409")
        self.assertTrue(step.snap["facts"]["L[L10].red_gate_allowed"]["stale"])
        self.assertIn("jump_confirmed", [e["type"] for e in step.ledger.events])

    def test_free_text_at_confirmation_escalates_again(self):
        step = self.make(self.layer_snapshot(), ["confirm_gate"],
                         {"confirm_gate": {"answers": {"decision": "go back to requirements please"}}})
        step.snap["pending"] = {"kind": "proposal", "text": "", "choices": {"Stop the run": {"action": "stop"}}}
        result = step.dispatch()
        self.assertEqual(result["next"], "hub")
        self.assertEqual(result["hub_input"]["escalation"]["trigger"], "free_text")
        self.assertEqual(step.snap["escalations"]["used"], 1)

    def test_hub_veto_returns_to_hub_once_then_human(self):
        snap = self.layer_snapshot()
        snap["facts"]["L[L10].red_gate_allowed"]["stale"] = True
        step = self.make(snap, ["hub"], {"hub": {"next": "implement", "reason": "r", "brief": "", "confidence": "high"}})
        first = step.dispatch()
        self.assertEqual(first["next"], "hub")
        self.assertEqual(first["hub_input"]["escalation"]["trigger"], "veto")
        second = step.handle_hub()
        self.assertEqual(second["next"], "confirm_gate")
        self.assertIn("Guard veto I1", second["confirm_questions"][0]["text"])
        self.assertNotIn("Accept: implement", second["confirm_questions"][0]["choices"])

    def test_escalation_cap_asks_human(self):
        step = self.make(self.layer_snapshot(), [])
        step.snap["escalations"] = {"used": 5, "max": 5}
        result = step.escalate("outcome", "blocked again")
        self.assertEqual(result["next"], "confirm_gate")
        self.assertIn("Continue (+5 escalations)", result["confirm_questions"][0]["choices"])

    def test_unanswered_confirmation_halts_then_reasks_on_resume(self):
        step = self.make(self.layer_snapshot(), ["confirm_gate"], {"confirm_gate": {"answers": {}, "outcome": "skipped_remaining"}})
        step.snap["pending"] = {"kind": "proposal", "text": "t", "choices": {"Stop the run": {"action": "stop"}}}
        with self.assertRaises(star_step.Halt):
            step.dispatch()
        self.assertEqual(step.snap["status"], "needs-human")
        resumed = step.dispatch()
        self.assertEqual(resumed["next"], "confirm_gate")

    def test_loop_escalates_after_two_identical_visits(self):
        snap = self.layer_snapshot()
        snap["facts"]["L[L10].implemented"] = fact(True, "implement", "L10")
        step = self.make(snap, [])
        digest = step.digest()
        step.snap["visits"] = [{"spoke": "verify", "layer": "L10", "digest": digest}] * 2
        result = step.route_after("implement")
        self.assertEqual(result["next"], "hub")
        self.assertEqual(result["hub_input"]["escalation"]["trigger"], "loop")

    def test_router_veto_is_a_table_bug(self):
        snap = self.layer_snapshot()
        snap["facts"]["L[L10].red_gate_allowed"]["stale"] = True
        snap["facts"]["L[L10].red_gate_allowed"]["value"] = True
        # Force a canonical match whose target the guard vetoes.
        snap["facts"]["L[L10].red_gate_allowed"] = fact(True, "red-gate", "L10")
        step = self.make(snap, [], )
        step.environment = environment(acceptance=(False, "contract changed"))
        result = step.route_after("red-gate")
        self.assertEqual(result["next"], "failed")
        self.assertIn("canonical table bug", result["reason"])

    def test_spoke_redirect_escalates_with_human_text(self):
        step = self.make(self.layer_snapshot(), ["layer_approval_gate"], {"layer_approval_gate": {
            "selected": "redirect", "additional_input": {"redirect": "the 409 test wins"}}})
        result = step.dispatch()
        self.assertEqual(result["next"], "hub")
        self.assertEqual(result["hub_input"]["escalation"]["human_text"], "the 409 test wins")

    def requirements_snapshot(self):
        snap = star_ledger.empty_snapshot()
        snap["facts"]["objective"] = fact("Cancel orders", "requirements")
        snap["objective"] = "Cancel orders"
        snap["cursor"] = {"spoke": "requirements", "phase": 1, "sub": 0, "layer": None}
        return snap

    def test_needs_human_from_agent_with_own_gate_goes_to_that_gate_not_the_hub(self):
        griller = {"artifact_path": "", "task_slug": "s", "multiple_slices": False, "blocker_count": 1,
                   "summary": "", "outcome": "needs-human", "reason": "ready for the human gate"}
        step = self.make(self.requirements_snapshot(), ["star_step", "requirements_griller"],
                         {"requirements_griller": griller})
        result = step.dispatch()
        self.assertEqual(result["next"], "requirements_gate")
        self.assertEqual(step.snap["escalations"]["used"], 0)
        self.assertIn("needs_human_to_gate", [e["type"] for e in step.ledger.events])

    def test_needs_human_without_own_gate_still_escalates(self):
        snap = self.layer_snapshot()
        snap["facts"]["L[L10].snapshot_tree"] = fact("abc", "implement", "L10")
        step = self.make(snap, ["implementor"], {"implementor": {
            "outcome": "needs-human", "reason": "new behavior", "checkpoint_required": True}})
        self.assertEqual(step.dispatch()["next"], "hub")

    def test_ask_human_offers_the_pending_spoke_gate_and_retry(self):
        griller = {"multiple_slices": False, "outcome": "blocked", "reason": "r"}
        step = self.make(self.requirements_snapshot(), ["hub"], {
            "requirements_griller": griller,
            "hub": {"next": "ask_human", "reason": "unclear", "brief": "", "confidence": "high", "alternatives": []}})
        step.snap["last"] = {"spoke": "requirements", "step": "requirements_griller", "outcome": "blocked"}
        result = step.dispatch()
        choices = result["confirm_questions"][0]["choices"]
        self.assertIn("Review in the spoke gate: requirements_gate", choices)
        self.assertIn("Retry: requirements", choices)
        self.assertGreater(len(choices), 1)
        step.outputs["confirm_gate"] = {"answers": {"decision": "Review in the spoke gate: requirements_gate"}}
        step.history = ["confirm_gate"]
        self.assertEqual(step.dispatch()["next"], "requirements_gate")

    def test_objective_gate_loops_on_empty_answer(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            step = star_step.StarStep({"history": ["graphify_update_gate"], "inputs": {"request": ""}, "outputs": {}},
                                      CATALOG, TABLE, environment())
            self.assertEqual(step.run()["next"], "objective_gate")
            ledger_path = step.ledger.path
            again = star_step.StarStep({"history": ["graphify_update_gate", "star_step", "objective_gate"],
                                        "inputs": {}, "outputs": {"objective_gate": {"additional_input": {"objective": "  "}}},
                                        "self": {"ledger_path": str(ledger_path)}}, CATALOG, TABLE, environment())
            self.assertEqual(again.run()["next"], "objective_gate")
            done = star_step.StarStep({"history": ["star_step", "objective_gate"], "inputs": {},
                                       "outputs": {"objective_gate": {"additional_input": {"objective": "Cancel orders"}}},
                                       "self": {"ledger_path": str(ledger_path)}}, CATALOG, TABLE, environment())
            result = done.run()
            self.assertEqual(result["next"], "requirements_griller")
            self.assertEqual(result["objective"], "Cancel orders")


class LayerDiffLedgerProtection(unittest.TestCase):
    def test_ledger_change_is_exempt_only_with_the_guard_sha(self):
        layer_diff = load_module("create_layer_diff_star", WORKFLOW_ROOT / "scripts" / "create-layer-diff.py")
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            import subprocess
            Path("src").mkdir()
            Path("src/a.py").write_text("a = 1\n")
            plan = Path(".github/plans/s")
            plan.mkdir(parents=True)
            todo = plan / "L10.todo.md"
            todo.write_text("## Implementation Boundary\n\n| Allowed | Forbidden |\n| --- | --- |\n| `src/` | `tests/` |\n")
            (plan / "star-state.json").write_text("{}\n")
            for args in (("init", "-q"), ("add", "."), ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "b")):
                subprocess.run(["git", *args], check=True, capture_output=True)
            snapshot_tree = layer_diff.current_tree()
            (plan / "star-state.json").write_text('{"guard": 1}\n')
            sha = star_ledger.file_sha(plan / "star-state.json")
            self.assertEqual(layer_diff.create(snapshot_tree, todo, 4096, ledger_sha=sha)["boundary_status"], "passed")
            tampered = layer_diff.create(snapshot_tree, todo, 4096, ledger_sha="0" * 64)
            self.assertEqual(tampered["boundary_status"], "violated")
            self.assertIn(".github/plans/s/star-state.json", tampered["boundary_violations"])


if __name__ == "__main__":
    unittest.main()
