#!/usr/bin/env python3
"""Center of layered-star.yaml: deterministic router plus guard in one step.

Reads a JSON payload on stdin (Conductor history, workflow inputs and the
outputs of every spoke step), records what the spoke that just returned
produced as facts in the ledger, and prints the next route as JSON.

- Canonical transitions come from ``star/canonical-table.json``.
- Frontier cases escalate to the hub (no rule, several rules, outcome not
  done, free text, loop), capped per run.
- Every proposal (router, hub or human) passes the guard (``star_guard``).
- Non-obvious hub jumps go to the generic ``confirm_gate`` with consequences.
- In unattended mode an unanswered confirmation stops the run with status
  ``needs-human``: the step fails its declared output schema on purpose so
  Conductor saves a checkpoint and ``ltdd resume`` (``conductor resume``)
  re-asks the pending question.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import star_guard  # noqa: E402
import star_ledger  # noqa: E402
from star_table import Catalog, Facts, Table, canonical_digest  # noqa: E402


ENTRY_STEPS = {
    "preflight_tests", "preflight_lint", "preflight_security", "preflight_failure_gate",
    "graphify_status", "graphify_update_gate", "graphify_refresh", "graphify_refresh_failure_gate",
}
HALT_PREFIX = "NEEDS_HUMAN"
LOOP_LIMIT = 2
TIERS = ["small", "standard", "escalated"]
EFFORT = {"small": "high", "standard": "medium", "escalated": "high"}
MAX_OBJECTIVE_ATTEMPTS = 5
CAP_EXTENSION = 5
GATE_ACTIONS_STOP = {"stop"}
GATE_ACTIONS_REDIRECT = {"redirect"}


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Side effects used by the guard (kept small so tests can replace them).
# ---------------------------------------------------------------------------
def git(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=False, capture_output=True, text=True, env=env)


def tree_hash() -> str:
    """Git tree of the working tree without ``.github/plans/`` (workflow state)."""
    descriptor, index_name = tempfile.mkstemp(prefix="star-tree-", suffix=".index")
    os.close(descriptor)
    Path(index_name).unlink()
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = index_name
    try:
        git(["read-tree", "HEAD"], environment)
        if git(["add", "-A"], environment).returncode != 0:
            return ""
        git(["rm", "-r", "-q", "--cached", "--ignore-unmatch", ".github/plans"], environment)
        result = git(["write-tree"], environment)
        return result.stdout.strip() if result.returncode == 0 else ""
    finally:
        Path(index_name).unlink(missing_ok=True)


def acceptance_check(state_path: str) -> tuple[bool, str]:
    try:
        module = load_script("star_slice_acceptance", "slice-acceptance.py")
        result = module.execute("check", Path(state_path))
    except (ValueError, KeyError, OSError, TypeError, SystemExit) as error:
        return False, str(error)
    return bool(result.get("proceed")), str(result.get("summary", ""))


def layer_boundary(snapshot_tree: str, todo_path: str) -> dict[str, Any]:
    try:
        module = load_script("star_layer_diff", "create-layer-diff.py")
        return module.create(snapshot_tree, Path(todo_path), 4096)
    except (SystemExit, OSError, ValueError) as error:
        return {"boundary_status": "error", "boundary_violations": [], "changed_files": [], "error": str(error)}


class Environment(star_guard.Environment):
    def __init__(self) -> None:
        self._tree: str | None = None
        self._acceptance: dict[str, tuple[bool, str]] = {}
        super().__init__(tree_hash=self.cached_tree, acceptance_check=self.cached_acceptance)

    def cached_tree(self) -> str:
        if self._tree is None:
            self._tree = tree_hash()
        return self._tree

    def cached_acceptance(self, state_path: str) -> tuple[bool, str]:
        if state_path not in self._acceptance:
            self._acceptance[state_path] = acceptance_check(state_path)
        return self._acceptance[state_path]

    def boundary(self, snapshot_tree: str, todo_path: str) -> dict[str, Any]:
        return layer_boundary(snapshot_tree, todo_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def frontmatter(path: str, key: str) -> str:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    if not content.startswith("---"):
        return ""
    end = content.find("\n---", 3)
    for line in content[3:end if end > 0 else None].splitlines():
        name, _, value = line.partition(":")
        if name.strip() == key:
            return value.strip().strip("\"'")
    return ""


def plan_dir_of(artifact_path: str) -> str:
    if not artifact_path:
        return ""
    path = Path(artifact_path)
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        relative = path
    parts = relative.parts
    if len(parts) >= 3 and parts[0] == ".github" and parts[1] == "plans":
        return str(Path(*parts[:3]))
    return str(path.parent)


def shorten(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    if isinstance(value, list) and len(value) > 8:
        return [shorten(item) for item in value[:8]] + [f"… {len(value) - 8} more"]
    return value


def exit_ok(output: dict[str, Any] | None) -> bool:
    return bool(output) and output.get("exit_code") == 0


class Halt(Exception):
    """Unattended run reached a question only a human may answer."""


# ---------------------------------------------------------------------------
# The step
# ---------------------------------------------------------------------------
class StarStep:
    def __init__(self, payload: dict[str, Any], catalog: Catalog, table: Table,
                 environment: Environment | star_guard.Environment) -> None:
        self.history: list[str] = list(payload.get("history") or [])
        self.inputs: dict[str, Any] = payload.get("inputs") or {}
        self.outputs: dict[str, Any] = {k: v for k, v in (payload.get("outputs") or {}).items() if v is not None}
        self.previous_self: dict[str, Any] = payload.get("self") or {}
        self.catalog = catalog
        self.table = table
        self.environment = environment
        self.ledger: star_ledger.Ledger | None = None
        self.result: dict[str, Any] = {}

    # -- small accessors ---------------------------------------------------
    @property
    def snap(self) -> dict[str, Any]:
        assert self.ledger is not None
        return self.ledger.snapshot

    @property
    def facts(self) -> Facts:
        return Facts(self.snap)

    def out(self, name: str) -> dict[str, Any]:
        value = self.outputs.get(name)
        return value if isinstance(value, dict) else {}

    def event(self, event_type: str, /, **data: Any) -> None:
        assert self.ledger is not None
        self.ledger.append(event_type, **data)

    def max_escalations(self) -> int:
        try:
            return max(0, int(self.inputs.get("max_escalations") or 5))
        except (TypeError, ValueError):
            return 5

    # -- ledger ------------------------------------------------------------
    def open_ledger(self) -> None:
        first_call = "star_step" not in self.history
        if not first_call and self.previous_self.get("ledger_path"):
            self.ledger = star_ledger.Ledger.load(Path(self.previous_self["ledger_path"]))
            return
        resume = (self.inputs.get("resume_path") or "").strip()
        slug = (self.inputs.get("task_slug") or "").strip()
        if resume:
            plan_dir = resume
        elif slug:
            plan_dir = str(Path(".github/plans") / slug)
        else:
            plan_dir = str(star_ledger.PENDING_DIR)
        path = star_ledger.ledger_path(plan_dir)
        if resume and path.is_file():
            self.ledger = star_ledger.Ledger.load(path)
            stale = [key for key, record in self.snap["facts"].items() if not record.get("stale") and key != "objective"]
            for key in stale:
                self.snap["facts"][key]["stale"] = True
            self.snap["visits"] = []
            self.snap["status"] = "running"
            self.snap["pending"] = None
            self.event("run_resumed", plan_dir=plan_dir, staled=stale)
            return
        archived = star_ledger.archive(path)
        self.ledger = star_ledger.Ledger(path)
        self.snap["plan_dir"] = "" if plan_dir == str(star_ledger.PENDING_DIR) else plan_dir
        self.snap["escalations"]["max"] = self.max_escalations()
        self.event("run_started", plan_dir=plan_dir, archived=str(archived) if archived else None,
                   inputs={k: shorten(v) for k, v in self.inputs.items() if k in {"request", "task_slug", "resume_path"}})

    def relocate(self, plan_dir: str) -> None:
        if not plan_dir or plan_dir == self.snap.get("plan_dir"):
            return
        assert self.ledger is not None
        target = star_ledger.ledger_path(plan_dir)
        if target.is_file() and target.resolve() != self.ledger.path.resolve():
            star_ledger.archive(target)
        self.snap["plan_dir"] = plan_dir
        self.ledger.relocate(target)

    # -- facts -------------------------------------------------------------
    def set_fact(self, name: str, value: Any, spoke: str, source: str = "script") -> None:
        facts = self.facts
        key = facts.key(name)
        phase, sub = self.catalog.position(spoke) if spoke in self.catalog else (0, 0)
        layer = facts.current_layer if name.startswith("layer.") else None
        previous = self.snap["facts"].get(key)
        record = {"value": value, "spoke": spoke, "phase": phase, "sub": sub, "layer": layer,
                  "source": source, "seq": len(self.ledger.events) + 1, "stale": False}
        self.snap["facts"][key] = record
        if previous is None or previous.get("value") != value or previous.get("stale"):
            self.event("fact", name=key, value=shorten(value), spoke=spoke, source=source)

    def digest(self) -> str:
        return canonical_digest({k: [v.get("value"), v.get("stale")] for k, v in self.snap["facts"].items()})

    def stale_targets(self, target: str) -> list[str]:
        """Facts that would become stale when moving back to ``target``."""
        if target not in self.catalog or target == "layer-select":
            return []
        cursor = self.snap["cursor"]
        if not cursor.get("spoke") or cursor["spoke"] not in self.catalog:
            return []
        target_position = self.catalog.position(target)
        if target_position >= self.catalog.position(cursor["spoke"]):
            return []
        phase, sub = target_position
        layer_phase = self.catalog.phases.index("layer")
        current_layer = self.facts.current_layer
        names = []
        for key, record in self.snap["facts"].items():
            if record.get("stale") or key == "objective":
                continue
            fact_position = (record.get("phase", 0), record.get("sub", 0))
            if record.get("phase", 0) > phase:
                names.append(key)
            elif (record.get("phase", 0) == phase and fact_position >= target_position
                  and (phase != layer_phase or record.get("layer") == current_layer)):
                names.append(key)
        return sorted(names)

    def apply_backtrack(self, target: str) -> list[str]:
        names = self.stale_targets(target)
        for key in names:
            self.snap["facts"][key]["stale"] = True
        if names:
            self.event("facts_staled", target=target, facts=names)
        if target in self.catalog and self.catalog.phase_index(target) < self.catalog.phases.index("layer"):
            layers = self.snap["layers"]
            if layers.get("selected") or layers.get("closed"):
                self.event("layers_reopened", selected=layers.get("selected"), closed=layers.get("closed"))
            self.snap["layers"] = {"current": None, "selected": [], "closed": []}
        return names

    # -- outcome of the spoke that returned --------------------------------
    def gate_decision(self, step: str) -> tuple[str, str]:
        output = self.out(step)
        selected = str(output.get("selected") or "")
        extra = output.get("additional_input") or {}
        text = ""
        if isinstance(extra, dict):
            text = str(extra.get("redirect") or extra.get("feedback") or "")
        return selected, text

    def agent_outcome(self, step: str, default: str = "done") -> tuple[str, str, dict[str, Any] | None]:
        output = self.out(step)
        outcome = str(output.get("outcome") or default)
        if outcome not in {"done", "blocked", "needs-human", "incoherence"}:
            outcome = "blocked"
        incoherence = output.get("incoherence") if outcome == "incoherence" else None
        if not isinstance(incoherence, dict) or not incoherence:
            incoherence = None if outcome != "incoherence" else {"claim": output.get("reason", ""), "source": step, "conflicts_with": ""}
        return outcome, str(output.get("reason") or output.get("summary") or ""), incoherence

    def returned(self, spoke: str, step: str) -> dict[str, Any]:
        """Extract outcome, decision and facts for the spoke that returned."""
        result = {"outcome": "done", "reason": "", "decision": "", "text": "", "incoherence": None, "artifacts": []}
        o = self.out
        f = lambda name, value, source="script": self.set_fact(name, value, spoke, source)  # noqa: E731

        def from_agent(agent_step: str) -> bool:
            outcome, reason, incoherence = self.agent_outcome(agent_step)
            artifacts = o(agent_step).get("artifacts")
            if isinstance(artifacts, list):
                result["artifacts"] = artifacts
            if outcome != "done":
                result.update(outcome=outcome, reason=reason, incoherence=incoherence)
                return True
            result["reason"] = reason
            return False

        def from_gate(gate_step: str, decision_fact: str | None = None) -> bool:
            selected, text = self.gate_decision(gate_step)
            result["decision"], result["text"] = selected, text
            if decision_fact and selected not in GATE_ACTIONS_STOP | GATE_ACTIONS_REDIRECT:
                f(decision_fact, selected, "human")
            return True

        if spoke == "requirements":
            agent = "requirements_reviser" if step.startswith("requirements_revis") else "requirements_griller"
            artifact = o(agent).get("artifact_path") or o("requirements_griller").get("artifact_path", "")
            if artifact:
                f("requirements.artifact", artifact, "agent")
                self.relocate(plan_dir_of(artifact))
            if o("requirements_griller"):
                f("requirements.multiple_slices", bool(o("requirements_griller").get("multiple_slices")), "agent")
            if step in {"requirements_griller", "requirements_reviser"}:
                if from_agent(step):
                    return result
                if step == "requirements_griller" and o(step).get("multiple_slices"):
                    result["decision"] = "multiple_slices"
                    f("requirements.decision", "multiple_slices", "agent")
                return result
            from_gate(step, "requirements.decision")
            return result

        if spoke == "triage":
            triage = o("fast_triage")
            paths = triage.get("allowed_paths") if isinstance(triage.get("allowed_paths"), list) else []
            route = str(triage.get("route") or "standard")
            f("triage.route", route, "agent")
            f("triage.allowed_paths", paths, "agent")
            f("triage.fast_bounded", route == "fast" and 1 <= len(paths) <= 3)
            from_agent("fast_triage")
            return result

        if spoke == "fast-change":
            check = o("fast_change_check")
            changed = check.get("changed_files") or []
            f("fast.changed_files", changed)
            f("fast.boundary_status", check.get("boundary_status", ""))
            if check and changed and check.get("boundary_status") != "passed":
                self.open_violation("I4", spoke, f"fast change outside allowed paths: {check.get('boundary_violations')}")
            elif check.get("boundary_status") == "passed":
                self.clear_violation("I4")
            green = all(exit_ok(o(name)) for name in ("fast_verification_tests", "fast_verification_lint", "fast_verification_security"))
            f("fast.checks_green", green)
            if step == "fast_approval_gate":
                from_gate(step, "fast.decision")
                return result
            if from_agent("fast_implementor"):
                return result
            if o("fast_implementor").get("needs_normal_path"):
                result.update(outcome="blocked", reason="fast implementor needs the normal path: " + str(o("fast_implementor").get("summary", "")))
            else:
                result.update(outcome="blocked", reason="fast change failed its boundary or checks")
            return result

        if spoke == "slice-selection":
            artifact = o("slice_run_starter").get("artifact_path", "")
            if artifact:
                f("slice.artifact", artifact, "agent")
                self.relocate(plan_dir_of(artifact))
            if step == "slice_run_starter" or step.endswith("_reviser"):
                from_agent(step)
                return result
            from_gate(step, "slice.decision" if step.startswith("slice_requirements") else None)
            return result

        if spoke == "acceptance-design":
            plan = o("slice_acceptance_planner").get("artifact_path", "")
            if plan:
                f("acceptance.plan_path", plan, "agent")
                try:
                    f("acceptance.mode", json.loads(Path(plan).read_text(encoding="utf-8")).get("mode", ""))
                except (OSError, ValueError, AttributeError):
                    pass
            if step == "slice_acceptance_planner":
                from_agent(step)
                return result
            from_gate(step, "acceptance.design_decision")
            return result

        if spoke == "acceptance-build":
            prepare = o("slice_acceptance_prepare")
            if prepare.get("state_path"):
                f("acceptance.state_path", prepare["state_path"])
                f("acceptance.regression_command", prepare.get("regression_command", ""))
            if step in {"slice_acceptance_prepare", "slice_acceptance_verify"}:
                result.update(outcome="blocked", reason=str(o(step).get("summary", "acceptance step blocked")))
                return result
            if step == "slice_acceptance_builder":
                from_agent(step)
                return result
            if step == "slice_acceptance_approve":
                approved = bool(o(step).get("proceed"))
                f("acceptance.approved", approved)
                if approved:
                    result["decision"] = "approve"
                    f("acceptance.build_decision", "approve", "human")
                else:
                    result.update(outcome="blocked", reason=str(o(step).get("summary", "")))
                return result
            from_gate(step, "acceptance.build_decision")
            return result

        if spoke == "layer-map":
            artifact = o("layer_map_reviser").get("artifact_path") or o("layer_mapper").get("artifact_path", "")
            if artifact:
                f("layer_map.path", artifact, "agent")
            if step in {"layer_mapper", "layer_map_reviser"}:
                from_agent(step)
                return result
            from_gate(step, "layer_map.decision")
            return result

        if spoke == "layer-select":
            if step == "layer_selection_gate":
                from_gate(step, "layer_select.decision")
                return result
            router = o("selected_layer_router")
            if not router.get("selected_layer") or router.get("exit_code") not in (None, 0):
                result.update(outcome="blocked", reason=str(router.get("stderr") or o("layer_selection_recorder").get("stderr") or "layer selection failed"))
                return result
            layer = str(router["selected_layer"])
            layers = self.snap["layers"]
            layers["current"] = layer
            if layer not in layers["selected"]:
                layers["selected"].append(layer)
            if layer in layers["closed"]:
                layers["closed"].remove(layer)
            self.event("layer_selected", layer=layer, work_kind=router.get("work_kind"))
            f("layer_select.decision", "selected", "human")
            f("layer.todo_path", router.get("todo_path", ""))
            f("layer.work_kind", router.get("work_kind", "behavior"))
            f("layer.layer_size", frontmatter(router.get("todo_path", ""), "layer_size") or "standard")
            result["decision"] = "selected"
            return result

        if spoke == "layer-todo":
            todo = o("layer_todo_reviser").get("artifact_path") or o("layer_todo_generator").get("artifact_path", "")
            if todo:
                size = frontmatter(todo, "layer_size")
                if size:
                    f("layer.layer_size", size)
            if step in {"layer_todo_generator", "layer_todo_reviser"}:
                from_agent(step)
                return result
            from_gate(step, "layer.todo_decision")
            return result

        if spoke == "test-author":
            if step == "test_author_preparer":
                result.update(outcome="blocked", reason=f"test-author handoff failed: {o(step).get('errors')}")
                return result
            recorder = o("test_author_completion_recorder")
            violations = recorder.get("violations") or []
            if violations:
                self.open_violation("I5", spoke, f"test author changed non-test files: {violations}")
            passed = bool(recorder.get("proceed")) and not recorder.get("checkpoint_required")
            f("layer.test_author_passed", passed)
            if from_agent("agent_test_author"):
                return result
            if recorder.get("checkpoint_required"):
                result.update(outcome="needs-human", reason=str(o("agent_test_author").get("checkpoint_summary", "")))
            elif not passed:
                result.update(outcome="blocked", reason=f"test author result rejected: {recorder.get('errors') or violations}")
            else:
                self.clear_violation("I5")
            return result

        if spoke == "red-gate":
            todo = o("layer_todo_generator").get("artifact_path", "")
            if step == "red_gate_decision_recorder":
                selected, text = self.gate_decision("red_suite_evidence_gate")
                result["decision"], result["text"] = selected, text
                f("layer.red_decision", selected, "human")
                f("layer.red_gate_allowed", bool(o(step).get("proceed")))
                f("layer.red_gate_state", frontmatter(todo, "red_gate_state"))
                return result
            from_gate(step, "layer.red_decision")
            return result

        if spoke in {"implement", "cleanup-implement"}:
            prefix = "" if spoke == "implement" else "cleanup_"
            snapshot_tree = o(f"{prefix}layer_snapshot").get("snapshot_tree", "")
            todo = o(f"{prefix}implementation_context").get("todo_path", "")
            if snapshot_tree:
                f("layer.snapshot_tree", snapshot_tree)
                boundary = self.environment.boundary(snapshot_tree, todo) if hasattr(self.environment, "boundary") else {}
                status = boundary.get("boundary_status")
                ledger_touched = self.ledger_touched(boundary.get("changed_files") or [])
                f("layer.implement_boundary", status)
                if status == "violated" or ledger_touched:
                    detail = boundary.get("boundary_violations") or []
                    if ledger_touched:
                        detail = [*detail, "star-state.json"]
                    self.open_violation("I3", spoke, f"implementation diff outside its boundary: {detail}")
            agent = f"{prefix}implementor"
            implementor = o(agent)
            if from_agent(agent):
                return result
            if implementor.get("checkpoint_required"):
                result.update(outcome="needs-human", reason=str(implementor.get("checkpoint_summary", "")))
                return result
            f("layer.implemented", True)
            return result

        if spoke in {"verify", "cleanup-verify"}:
            prefix = "" if spoke == "verify" else "cleanup_"
            checks = [f"{prefix}verification_tests", f"{prefix}verification_lint", f"{prefix}verification_security"]
            green = all(exit_ok(o(name)) for name in checks)
            if spoke == "cleanup-verify":
                green = green and bool(o("cleanup_result_check").get("passed"))
            diff = o(f"{prefix}layer_diff")
            status = diff.get("boundary_status", "")
            if status == "violated":
                self.open_violation("I3", spoke, f"layer diff outside its boundary: {diff.get('boundary_violations')}")
            elif status in {"passed", "unavailable"} and self.snap.get("violation", {}) and self.snap["violation"].get("rule") == "I3":
                self.clear_violation("I3")
            verified = green and status != "violated"
            f("layer.verify_green", verified)
            f("layer.verify_tree", self.environment.tree_hash() if verified else "")
            if not verified:
                failing = [name for name in checks if not exit_ok(o(name))]
                result.update(outcome="blocked", reason=f"verification not green: failing={failing} boundary={status}")
            return result

        if spoke in {"layer-review", "cleanup-review"}:
            agent = "layer_reviewer" if spoke == "layer-review" else "cleanup_reviewer"
            if step == agent:
                from_agent(step)
                return result
            selected, text = self.gate_decision(step)
            result["decision"], result["text"] = selected, text
            if selected in GATE_ACTIONS_STOP | GATE_ACTIONS_REDIRECT:
                return result
            if selected.startswith("approve"):
                veto = star_guard.verification_matches_tree(self.facts, self.environment)
                if veto is not None:
                    self.event("veto", proposer="router", target="close-layer", **veto.as_dict())
                    result.update(outcome="blocked", reason=f"cannot close layer: {veto.message}")
                    return result
                layer = self.facts.current_layer
                if layer and layer not in self.snap["layers"]["closed"]:
                    self.snap["layers"]["closed"].append(layer)
                    self.event("layer_closed", layer=layer, spoke=spoke)
            f("layer.review_decision", selected, "human")
            return result

        if spoke == "cleanup-plan":
            if step in {"cleanup_planner", "cleanup_plan_reviser"}:
                if not from_agent(step):
                    result.update(outcome="blocked", reason=f"cleanup plan not ready: {o(step).get('blockers')}")
                return result
            if step == "cleanup_plan_validator":
                result.update(outcome="blocked", reason=f"cleanup plan invalid: {o(step).get('errors')}")
                return result
            if step == "cleanup_baseline_security":
                green = all(exit_ok(o(name)) for name in ("cleanup_baseline_tests", "cleanup_baseline_lint", "cleanup_baseline_security"))
                f("layer.cleanup_baseline_green", green)
                gate = "cleanup_plan_revision_gate" if self.later("cleanup_plan_revision_gate", "cleanup_plan_gate") else "cleanup_plan_gate"
                selected, _ = self.gate_decision(gate)
                f("layer.cleanup_plan_decision", selected or "approve_cleanup", "human")
                result["decision"] = selected
                if not green:
                    result.update(outcome="blocked", reason="cleanup baseline is not green")
                return result
            from_gate(step, "layer.cleanup_plan_decision")
            return result

        if spoke == "final":
            if step == "slice_acceptance_final":
                f("final.acceptance_green", False)
                result.update(outcome="blocked", reason=str(o(step).get("summary", "final slice acceptance failed")))
                return result
            f("final.acceptance_green", bool(o("slice_acceptance_final").get("proceed")))
            if from_agent("final_reviewer"):
                return result
            f("final.reviewed", True, "agent")
            return result

        if spoke == "memory":
            if step == "memory_capturer":
                result["decision"] = "capture"
                f("memory.decision", "capture", "human")
                from_agent(step)
                return result
            from_gate(step, "memory.decision")
            return result

        result.update(outcome="blocked", reason=f"no extractor for spoke {spoke}")
        return result

    def later(self, first: str, second: str) -> bool:
        """True when ``first`` ran more recently than ``second``."""
        for name in reversed(self.history):
            if name == first:
                return True
            if name == second:
                return False
        return False

    def ledger_touched(self, changed: list[str]) -> bool:
        return any(Path(path).name == star_ledger.LEDGER_NAME for path in changed)

    def open_violation(self, rule: str, spoke: str, message: str) -> None:
        position = list(self.catalog.position(spoke))
        self.snap["violation"] = {"rule": rule, "spoke": spoke, "position": position, "message": shorten(message, 400),
                                  "layer": self.facts.current_layer}
        self.event("violation_opened", rule=rule, spoke=spoke, message=shorten(message, 400))

    def clear_violation(self, rule: str) -> None:
        violation = self.snap.get("violation")
        if violation and violation.get("rule") == rule:
            self.snap["violation"] = None
            self.event("violation_cleared", rule=rule)

    # -- routing -----------------------------------------------------------
    def last_spoke(self) -> str:
        return str(self.snap.get("last", {}).get("spoke") or "")

    def consecutive_visits(self, spoke: str) -> int:
        digest = self.digest()
        layer = self.facts.current_layer if self.catalog.layer_scoped(spoke) else None
        count = 0
        for visit in reversed(self.snap["visits"]):
            if visit["spoke"] == spoke and visit.get("layer") == layer and visit.get("digest") == digest:
                count += 1
            else:
                break
        return count

    def visited(self, spoke: str) -> bool:
        layer = self.facts.current_layer if self.catalog.layer_scoped(spoke) else None
        return any(v["spoke"] == spoke and (layer is None or v.get("layer") == layer) for v in self.snap["visits"])

    def model_tier(self, spoke: str, retry_after_blocked: bool) -> str:
        base = "small" if self.facts.get("layer.layer_size") == "small" else "standard"
        if retry_after_blocked:
            return TIERS[min(TIERS.index(base) + 1, len(TIERS) - 1)]
        return base

    def enter(self, target: str, *, proposer: str, brief: str = "", rule: str = "") -> dict[str, Any]:
        last = self.snap.get("last", {})
        staled = self.apply_backtrack(target)
        retry = last.get("spoke") == target and last.get("outcome") == "blocked"
        tier = self.model_tier(target, retry)
        spoke = self.catalog.spokes[target]
        entry = spoke.get("reopen_entry") if spoke.get("reopen_entry") and self.visited(target) else spoke["entry"]
        layer = self.facts.current_layer if self.catalog.layer_scoped(target) else None
        if proposer != "router":
            for item in self.snap["incoherences"]:
                if item.get("status") == "open" and not item.get("resolve_spoke"):
                    item["resolve_spoke"] = target
        self.snap["visits"].append({"spoke": target, "layer": layer, "digest": self.digest()})
        self.snap["visits"] = self.snap["visits"][-50:]
        phase, sub = self.catalog.position(target)
        self.snap["cursor"] = {"spoke": target, "phase": phase, "sub": sub, "layer": layer}
        self.snap["entered"] = {"spoke": target, "entry": entry, "brief": brief, "proposer": proposer}
        self.snap["pending"] = None
        self.snap["hub_request"] = None
        self.snap["hub_vetoes"] = 0
        self.snap["status"] = "running"
        self.event("spoke_entered", spoke=target, entry=entry, layer=layer, proposer=proposer, rule=rule,
                   model_tier=tier, brief=shorten(brief, 400), staled=staled)
        return {"next": entry, "spoke": target, "brief": brief, "model_tier": tier,
                "reasoning_effort": EFFORT[tier], "reason": f"{proposer}:{rule or target}"}

    def finish(self, status: str, next_step: str, reason: str) -> dict[str, Any]:
        self.snap["status"] = status
        self.event("run_finished", status=status, reason=shorten(reason, 400))
        return {"next": next_step, "reason": reason}

    def route_after(self, spoke: str) -> dict[str, Any]:
        """Apply the canonical table after ``spoke`` (or ``entry``) returned done."""
        matches = self.table.match(spoke, self.facts)
        if not matches:
            return self.escalate("no_rule", f"no canonical rule after {spoke}")
        if len(matches) > 1:
            return self.escalate("multiple_rules", f"rules {[m['id'] for m in matches]} apply after {spoke}")
        rule = matches[0]
        target = rule["next"]
        if target == "$done":
            return self.finish("done", "done", f"rule {rule['id']}: slice complete")
        if self.consecutive_visits(target) >= LOOP_LIMIT:
            return self.escalate("loop", f"{target} would run a third time without fact changes")
        veto = star_guard.check(target, self.snap, self.catalog, self.environment)
        if veto is not None:
            self.event("veto", proposer="router", target=target, rule_id=rule["id"], **veto.as_dict())
            return self.finish("failed", "failed", f"canonical table bug: rule {rule['id']} -> {target} vetoed by {veto.rule}: {veto.message}")
        return self.enter(target, proposer="router", rule=rule["id"])

    # -- escalation and the hub --------------------------------------------
    def escalate(self, trigger: str, detail: str, **extra: Any) -> dict[str, Any]:
        escalations = self.snap["escalations"]
        if escalations["used"] >= escalations["max"]:
            self.event("escalation_cap_reached", trigger=trigger, used=escalations["used"], max=escalations["max"])
            return self.ask_human({
                "kind": "cap", "trigger": trigger, "detail": detail, "extra": extra,
                "text": (f"The run reached its escalation cap ({escalations['max']}). "
                         f"Pending escalation: {trigger} — {detail}. Continue with {CAP_EXTENSION} more escalations or stop?"),
                "choices": {f"Continue (+{CAP_EXTENSION} escalations)": {"action": "continue"}, "Stop the run": {"action": "stop"}},
                "allow_free_text": False,
            })
        escalations["used"] += 1
        self.snap["hub_request"] = {"trigger": trigger, "detail": detail, **extra}
        self.event("escalation", trigger=trigger, detail=shorten(detail, 400), used=escalations["used"],
                   max=escalations["max"], **{k: shorten(v, 400) for k, v in extra.items()})
        return {"next": "hub", "reason": f"escalation:{trigger}", "hub_input": self.hub_input()}

    def hub_input(self) -> dict[str, Any]:
        facts = {key: shorten(record.get("value")) for key, record in self.snap["facts"].items() if not record.get("stale")}
        stale = sorted(key for key, record in self.snap["facts"].items() if record.get("stale"))
        legal = [name for name in self.catalog.spokes if star_guard.legal(name, self.snap, self.catalog, self.environment)]
        escalations = self.snap["escalations"]
        return {
            "objective": self.snap.get("objective", ""),
            "slice_target": self.snap.get("plan_dir", ""),
            "escalation": self.snap.get("hub_request"),
            "last_spoke": self.snap.get("last", {}),
            "cursor": self.snap.get("cursor"),
            "current_layer": self.facts.current_layer,
            "facts": facts,
            "stale_facts": stale,
            "open_incoherences": [i for i in self.snap["incoherences"] if i.get("status") == "open"],
            "open_violation": self.snap.get("violation"),
            "recent_events": [{"type": e["type"], **{k: shorten(v, 200) for k, v in e["data"].items()}} for e in self.ledger.events[-10:]],
            "catalog": self.catalog.describe(),
            "legal_now": legal,
            "escalations_remaining": escalations["max"] - escalations["used"],
        }

    def auto_accept(self, target: str, confidence: str) -> bool:
        if confidence not in {"medium", "high"}:
            return False
        origin = self.last_spoke()
        default_next = self.catalog.spokes.get(origin, {}).get("default_next") if origin else None
        if target not in {origin, default_next} or not origin:
            return False
        if target == "layer-select":
            layer = self.facts.current_layer
            if layer and layer not in self.snap["layers"]["closed"]:
                return False
        if self.consecutive_visits(target) >= LOOP_LIMIT:
            return False
        return not self.stale_targets(target)

    def handle_hub(self) -> dict[str, Any]:
        hub = self.out("hub")
        target = str(hub.get("next") or "ask_human")
        confidence = str(hub.get("confidence") or "low")
        brief = str(hub.get("brief") or "")
        reason = str(hub.get("reason") or "")
        alternatives = [a for a in (hub.get("alternatives") or []) if isinstance(a, dict) and a.get("next")][:2]
        request = self.snap.get("hub_request") or {}
        self.event("hub_proposal", next=target, confidence=confidence, reason=shorten(reason, 400),
                   brief=shorten(brief, 400), alternatives=[shorten(a, 200) for a in alternatives])
        proposal = {"target": target, "reason": reason, "brief": brief, "confidence": confidence,
                    "alternatives": alternatives, "trigger": request.get("trigger"), "human_text": request.get("human_text", "")}
        if target in {"stop", "ask_human"}:
            return self.confirm(proposal)
        veto = star_guard.check(target, self.snap, self.catalog, self.environment)
        if veto is not None:
            self.event("veto", proposer="hub", target=target, **veto.as_dict())
            if self.snap.get("hub_vetoes", 0) == 0:
                self.snap["hub_vetoes"] = 1
                return self.escalate("veto", f"hub proposal {target} vetoed by {veto.rule}", veto=veto.as_dict(),
                                     previous_proposal={"next": target, "reason": reason})
            self.snap["hub_vetoes"] = 0
            proposal["veto"] = veto.as_dict()
            return self.confirm(proposal)
        if self.auto_accept(target, confidence):
            self.event("jump_auto_accepted", target=target, confidence=confidence)
            return self.enter(target, proposer="hub", brief=brief)
        return self.confirm(proposal)

    # -- confirmation gate -------------------------------------------------
    def confirm(self, proposal: dict[str, Any]) -> dict[str, Any]:
        target = proposal.get("target")
        lines = []
        if proposal.get("human_text"):
            lines.append(f"Understood: “{shorten(proposal['human_text'], 300)}”.")
        choices: dict[str, dict[str, Any]] = {}
        if target == "stop":
            lines.append(f"The hub proposes to stop the run: {proposal.get('reason', '')}")
        elif target == "ask_human":
            lines.append(f"The hub asks you to decide: {proposal.get('reason', '')}")
        else:
            lines.append(f"Proposal: **{target}** (confidence {proposal.get('confidence')}). Reason: {proposal.get('reason', '')}")
            if proposal.get("brief"):
                lines.append(f"Brief for {target}: {shorten(proposal['brief'], 600)}")
            consequences = self.stale_targets(target) if target in self.catalog else []
            lines.append("Consequences: " + (f"these facts become stale and must be re-verified: {', '.join(consequences)}" if consequences else "no fact becomes stale."))
            if proposal.get("veto"):
                veto = proposal["veto"]
                lines.append(f"Guard veto {veto['rule']}: {veto['message']} Missing: {', '.join(veto.get('missing') or []) or '—'}. Choose again.")
            else:
                choices[f"Accept: {target}"] = {"action": "enter", "target": target, "brief": proposal.get("brief", "")}
        for alternative in proposal.get("alternatives") or []:
            name = str(alternative.get("next"))
            if name in self.catalog and name != target and star_guard.legal(name, self.snap, self.catalog, self.environment):
                label = f"Alternative: {name} — {shorten(str(alternative.get('reason', '')), 100)}"
                choices[label] = {"action": "enter", "target": name, "brief": str(alternative.get("reason", ""))}
        choices["Stop the run"] = {"action": "stop"}
        lines.append("Or write something else: free text goes back to the hub and counts as an escalation.")
        return self.ask_human({"kind": "proposal", "proposal": proposal, "text": "\n\n".join(lines),
                               "choices": choices, "allow_free_text": True})

    def ask_human(self, pending: dict[str, Any]) -> dict[str, Any]:
        self.snap["pending"] = pending
        self.snap["status"] = "awaiting-human"
        self.event("confirmation_requested", kind=pending["kind"], choices=list(pending["choices"]))
        return {"next": "confirm_gate", "reason": f"confirm:{pending['kind']}",
                "confirm_prompt": pending["text"],
                "confirm_questions": [{
                    "id": "decision", "text": pending["text"], "choices": list(pending["choices"]),
                    "allow_free_text": bool(pending.get("allow_free_text")), "required": True,
                }]}

    def handle_confirm(self) -> dict[str, Any]:
        pending = self.snap.get("pending")
        if not pending:
            return self.route_after(self.last_spoke() or "entry")
        answers = self.out("confirm_gate").get("answers") or {}
        answer = str(answers.get("decision") or "").strip() if isinstance(answers, dict) else ""
        if not answer:
            if self.snap.get("status") == "needs-human":
                self.event("confirmation_reasked", kind=pending["kind"])
                return self.ask_human(pending)
            self.snap["status"] = "needs-human"
            self.event("run_stopped", status="needs-human", reason="unattended run cannot answer the confirmation gate",
                       pending=pending.get("kind"))
            raise Halt(pending.get("text", ""))
        choice = pending["choices"].get(answer)
        if choice is None:
            self.event("human_redirect", text=shorten(answer, 400))
            self.snap["pending"] = None
            return self.escalate("free_text", "human wrote free text at the confirmation gate", human_text=answer)
        action = choice["action"]
        if action == "stop":
            self.event("run_stopped", status="stopped", by="human", at="confirm_gate")
            return self.finish("stopped", "stopped", "Human stopped the star run at the confirmation gate.")
        if action == "continue":
            self.snap["escalations"]["max"] += CAP_EXTENSION
            self.event("escalation_cap_extended", max=self.snap["escalations"]["max"])
            self.snap["pending"] = None
            return self.escalate(pending.get("trigger", "no_rule"), pending.get("detail", ""), **(pending.get("extra") or {}))
        target = choice["target"]
        veto = star_guard.check(target, self.snap, self.catalog, self.environment)
        if veto is not None:
            self.event("veto", proposer="human", target=target, **veto.as_dict())
            proposal = dict(pending.get("proposal") or {})
            proposal.update(target=target, veto=veto.as_dict())
            return self.confirm(proposal)
        self.event("jump_confirmed", target=target, label=shorten(answer, 200))
        return self.enter(target, proposer="human", brief=choice.get("brief", ""))

    # -- spoke return --------------------------------------------------------
    def handle_spoke(self, spoke: str, step: str) -> dict[str, Any]:
        entered = self.snap.get("entered") or {}
        result = self.returned(spoke, step)
        outcome = result["outcome"]
        decision = result["decision"]
        self.snap["last"] = {"spoke": spoke, "outcome": outcome, "reason": shorten(result["reason"], 400),
                             "decision": decision, "step": step, "layer": self.facts.current_layer}
        self.event("spoke_returned", spoke=spoke, step=step, outcome=outcome, decision=decision,
                   reason=shorten(result["reason"], 400), artifacts=shorten(result["artifacts"]),
                   layer=self.facts.current_layer, brief_used=bool(entered.get("brief")))
        if decision in GATE_ACTIONS_STOP:
            self.event("run_stopped", status="stopped", by="human", at=step)
            return self.finish("stopped", "stopped", f"Human stopped the star run at {step}.")
        if decision in GATE_ACTIONS_REDIRECT:
            text = result["text"].strip()
            self.event("human_redirect", spoke=spoke, step=step, text=shorten(text, 400))
            return self.escalate("free_text", f"human redirect at {step}", human_text=text)
        if outcome == "incoherence":
            item = {"id": f"INC{len(self.snap['incoherences']) + 1}", "status": "open", "spoke": spoke,
                    "layer": self.facts.current_layer, **(result["incoherence"] or {})}
            self.snap["incoherences"].append(item)
            self.event("incoherence_detected", **{k: shorten(v, 400) for k, v in item.items()})
        if outcome != "done":
            return self.escalate("outcome", f"{spoke} returned {outcome}", outcome=outcome, reason=result["reason"],
                                 incoherence=result["incoherence"])
        for item in self.snap["incoherences"]:
            if item.get("status") == "open" and item.get("resolve_spoke") == spoke:
                item["status"] = "resolved"
                self.event("incoherence_resolved", id=item["id"], spoke=spoke)
        return self.route_after(spoke)

    # -- objective ------------------------------------------------------------
    def entry(self) -> dict[str, Any]:
        if self.out("preflight_failure_gate").get("selected") == "continue_with_waiver":
            self.set_fact("preflight.waived", True, "requirements", "human")
        graph = self.out("graphify_refresh") or self.out("graphify_status")
        if graph:
            self.set_fact("graph.available", bool(graph.get("graph_available")), "requirements")
        request = str(self.inputs.get("request") or "").strip()
        if request and not self.facts.get("objective"):
            self.set_objective(request, "input")
        if not self.facts.get("objective"):
            return {"next": "objective_gate", "reason": "objective missing"}
        return self.route_after("entry")

    def set_objective(self, text: str, source: str) -> None:
        self.snap["objective"] = text
        self.snap["facts"]["objective"] = {"value": text, "spoke": "requirements", "phase": 0, "sub": 0, "layer": None,
                                           "source": source, "seq": len(self.ledger.events) + 1, "stale": False}
        self.event("objective_set", source=source, objective=shorten(text, 400))

    def handle_objective(self) -> dict[str, Any]:
        output = self.out("objective_gate")
        extra = output.get("additional_input") or {}
        text = str(extra.get("objective") or "").strip() if isinstance(extra, dict) else ""
        if not text:
            self.snap["objective_attempts"] = self.snap.get("objective_attempts", 0) + 1
            self.event("objective_empty", attempts=self.snap["objective_attempts"])
            if self.snap["objective_attempts"] >= MAX_OBJECTIVE_ATTEMPTS:
                self.snap["status"] = "needs-human"
                raise Halt("No objective after repeated empty answers; run ltdd run with a request.")
            return {"next": "objective_gate", "reason": "objective empty"}
        self.set_objective(text, "objective_gate")
        return self.route_after("entry")

    # -- main -------------------------------------------------------------------
    def dispatch(self) -> dict[str, Any]:
        previous = self.history[-1] if self.history else ""
        if previous == "objective_gate":
            return self.handle_objective()
        if previous == "hub":
            return self.handle_hub()
        if previous == "confirm_gate":
            return self.handle_confirm()
        if previous in self.catalog.step_to_spoke:
            return self.handle_spoke(self.catalog.step_to_spoke[previous], previous)
        if self.snap.get("pending"):
            return self.ask_human(self.snap["pending"])
        return self.entry()

    def run(self) -> dict[str, Any]:
        try:
            self.open_ledger()
        except star_ledger.LedgerError as error:
            return {"next": "failed", "reason": f"ledger integrity check failed: {error}", "objective": ""}
        try:
            result = self.dispatch()
            halted = None
        except Halt as halt:
            result = {"next": f"{HALT_PREFIX}: run `ltdd resume` to answer the pending confirmation", "reason": str(halt)}
            halted = True
        sha = self.ledger.save()
        result.setdefault("brief", "")
        result.setdefault("model_tier", self.model_tier("", False))
        result.setdefault("reasoning_effort", EFFORT[result["model_tier"]])
        result.setdefault("confirm_prompt", "")
        result.setdefault("confirm_questions", [])
        result.setdefault("hub_input", {})
        result.setdefault("spoke", "")
        result.update(objective=self.snap.get("objective", ""), ledger_path=str(self.ledger.path), ledger_sha=sha,
                      status=self.snap.get("status", ""), plan_dir=self.snap.get("plan_dir", ""))
        if halted:
            print(f"star_step: run stopped with status needs-human; ledger {self.ledger.path}. "
                  "Resume with `ltdd resume` to answer the pending confirmation.", file=sys.stderr)
        return result


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as error:
        print(json.dumps({"next": "failed", "reason": f"invalid star_step payload: {error}"}))
        return
    try:
        catalog = Catalog.load()
        table = Table.load(catalog)
        result = StarStep(payload, catalog, table, Environment()).run()
    except Exception as error:  # fail closed with a readable reason
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = {"next": "failed", "reason": f"star_step internal error: {type(error).__name__}: {error}"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
