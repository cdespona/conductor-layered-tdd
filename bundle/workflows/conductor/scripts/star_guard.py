"""Deterministic guard of the star workflow: invariants I1-I10.

The guard decides whether a spoke may be entered. Post-conditions of spokes
that touch code are recorded as facts by ``star-step.py`` right after the
spoke returns; an open violation blocks any forward move. The canonical table
cannot relax these checks and there is no override.

- I1  No production-code spoke without an allowed red gate or an exemption.
- I2  No implementation (or review) when the slice acceptance contract changed,
      except in preservation mode or the fast path.
- I3  After implement/cleanup-implement the diff stays inside the boundary.
- I4  After fast-change the diff stays inside triage allowed paths.
- I5  The test author only writes tests.
- I6  No layer review or close without green verification on the current tree.
- I7  No final with unclosed layers or open incoherences.
- I8  No memory without a final review (capture needs the human memory gate).
- I9  A stale fact never satisfies a condition (see ``star_table.Facts``).
- I10 The proposed target exists in the spoke catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from star_table import Catalog, Facts, evaluate


CODE_SPOKES = {"implement", "fast-change", "cleanup-implement"}
ACCEPTANCE_GUARDED = {"implement", "cleanup-implement", "layer-review", "cleanup-review"}
REVIEW_SPOKES = {"layer-review", "cleanup-review"}


@dataclass
class Veto:
    rule: str
    message: str
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "message": self.message, "missing": self.missing}


@dataclass
class Environment:
    """Side-effect hooks so the invariants stay testable."""

    tree_hash: Callable[[], str] = lambda: ""
    acceptance_check: Callable[[str], tuple[bool, str]] = lambda state_path: (False, "no acceptance check available")


def missing_facts(facts: Facts, names: list[str]) -> list[str]:
    result = []
    for name in names:
        record = facts.record(name)
        if record is None:
            result.append(name)
        elif record.get("stale"):
            result.append(f"{name} (stale)")
    return result


def i1(target: str, facts: Facts) -> Veto | None:
    if target not in CODE_SPOKES:
        return None
    if target == "fast-change":
        if facts.get("triage.route") == "fast" and facts.get("triage.fast_bounded") is True:
            return None
        return Veto("I1", "fast-change needs a bounded fast triage (exemption).",
                    missing_facts(facts, ["triage.route", "triage.fast_bounded"]))
    if target == "cleanup-implement":
        if (facts.get("layer.work_kind") == "cleanup"
                and facts.get("layer.cleanup_plan_decision") == "approve_cleanup"
                and facts.get("layer.cleanup_baseline_green") is True):
            return None
        return Veto("I1", "cleanup-implement needs a human-approved cleanup layer with a green baseline.",
                    missing_facts(facts, ["layer.work_kind", "layer.cleanup_plan_decision", "layer.cleanup_baseline_green"]))
    if facts.get("layer.red_gate_allowed") is True:
        return None
    if (facts.get("layer.work_kind") == "infrastructure"
            and facts.get("layer_map.decision") == "approve"
            and facts.get("layer.todo_decision") == "approve_infrastructure"):
        return None
    return Veto("I1", "implement needs an allowed red gate or an approved infrastructure layer.",
                missing_facts(facts, ["layer.red_gate_allowed"]))


def i2(target: str, facts: Facts, environment: Environment) -> Veto | None:
    if target not in ACCEPTANCE_GUARDED:
        return None
    if facts.get("acceptance.mode") == "preservation":
        return None
    state_path = facts.get("acceptance.state_path")
    if not state_path:
        return Veto("I2", "the approved slice acceptance state is unknown.",
                    missing_facts(facts, ["acceptance.state_path"]))
    passed, summary = environment.acceptance_check(state_path)
    if passed:
        return None
    return Veto("I2", f"slice acceptance contract check failed: {summary}", ["acceptance.contract_unchanged"])


def open_violation(target: str, snapshot: dict[str, Any], catalog: Catalog) -> Veto | None:
    violation = snapshot.get("violation")
    if not violation:
        return None
    position = tuple(violation.get("position") or (0, 0))
    if catalog.position(target) <= position:
        return None
    return Veto(violation.get("rule", "I3"),
                f"open {violation.get('rule')} violation blocks any advance: {violation.get('message')}",
                ["violation cleared by a passing post-condition check"])


def i6(target: str, facts: Facts, environment: Environment) -> Veto | None:
    if target not in REVIEW_SPOKES:
        return None
    return verification_matches_tree(facts, environment)


def verification_matches_tree(facts: Facts, environment: Environment) -> Veto | None:
    if facts.get("layer.verify_green") is not True:
        return Veto("I6", "the current layer has no green verification.",
                    missing_facts(facts, ["layer.verify_green"]) or ["layer.verify_green (false)"])
    verified = facts.get("layer.verify_tree")
    current = environment.tree_hash()
    if not verified or verified != current:
        return Veto("I6", "the tree changed after verification; verify again.",
                    ["layer.verify_tree == current tree"])
    return None


def i7(target: str, snapshot: dict[str, Any]) -> Veto | None:
    if target != "final":
        return None
    layers = snapshot.get("layers", {})
    unclosed = [layer for layer in layers.get("selected", []) if layer not in layers.get("closed", [])]
    open_items = [item for item in snapshot.get("incoherences", []) if item.get("status") == "open"]
    if not layers.get("closed"):
        return Veto("I7", "no layer has been closed yet.", ["layers.closed"])
    if unclosed:
        return Veto("I7", f"layers still open: {', '.join(unclosed)}", [f"layer {layer} closed" for layer in unclosed])
    if open_items:
        return Veto("I7", f"{len(open_items)} incoherence(s) still open.",
                    [f"incoherence {item['id']} resolved" for item in open_items])
    if snapshot.get("violation"):
        return Veto("I7", "an open violation blocks final.", ["violation cleared"])
    return None


def i8(target: str, facts: Facts) -> Veto | None:
    if target != "memory":
        return None
    if facts.get("final.reviewed") is True and facts.get("final.acceptance_green") is True:
        return None
    return Veto("I8", "memory needs green final acceptance and a final review.",
                missing_facts(facts, ["final.acceptance_green", "final.reviewed"]))


def precondition(target: str, facts: Facts, catalog: Catalog) -> Veto | None:
    spoke = catalog.spokes[target]
    if evaluate(spoke.get("requires"), facts):
        return None
    return Veto("PRE", f"precondition of {target} not met: {spoke.get('preconditions', '')}",
                [str(spoke.get("requires"))])


def check(target: str, snapshot: dict[str, Any], catalog: Catalog,
          environment: Environment | None = None) -> Veto | None:
    """Return the first veto for entering ``target`` or None when entry is legal."""
    environment = environment or Environment()
    if target not in catalog:
        return Veto("I10", f"unknown spoke {target!r}", [])
    facts = Facts(snapshot)
    for veto in (
        open_violation(target, snapshot, catalog),
        i1(target, facts),
        i6(target, facts, environment),
        i7(target, snapshot),
        i8(target, facts),
        precondition(target, facts, catalog),
        i2(target, facts, environment),
    ):
        if veto is not None:
            return veto
    return None


def legal(target: str, snapshot: dict[str, Any], catalog: Catalog,
          environment: Environment | None = None) -> bool:
    return check(target, snapshot, catalog, environment) is None
