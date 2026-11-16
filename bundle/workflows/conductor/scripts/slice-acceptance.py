#!/usr/bin/env python3
"""Record bounded acceptance support, verification and human approval before layers."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path, PurePosixPath


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snapshot = load("acceptance_snapshot", "capture-layer-snapshot.py")
diff = load("acceptance_diff", "create-layer-diff.py")
verification = load("acceptance_verification", "run-verification.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def instruction_path(value):
    path = PurePosixPath(value)
    return (value.startswith(("workflows/", ".github/instructions/", ".github/skills/"))
            or path.name in {"AGENTS.md", "CLAUDE.md"}
            or value == ".github/copilot-instructions.md")


def paths(values, forbidden=False):
    # Forbidden files only ever narrow the scope, so naming instruction or
    # workflow files there is a safe, explicit reminder rather than a grant.
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise ValueError("file scope must be an array of exact repository-relative paths")
    root = Path.cwd().resolve()
    for value in values:
        path = PurePosixPath(value)
        if (path.is_absolute() or ".." in path.parts or value != path.as_posix()
                or any(c in value for c in "*?[]") or value.startswith(".git/")
                or (not forbidden and instruction_path(value))
                or not (root / value).resolve().is_relative_to(root)
                or (root / value).is_symlink() or (root / value).is_dir()):
            raise ValueError(f"invalid acceptance file scope: {value}")
    return sorted(set(values))


def read_plan(path):
    plan = json.loads(path.read_text())
    if not isinstance(plan, dict):
        raise ValueError("acceptance plan must be a JSON object")
    if plan.get("mode") not in {"behavior", "preservation"}:
        raise ValueError("mode must be behavior or preservation")
    if plan.get("unresolved_questions") != []:
        raise ValueError("resolve all acceptance design questions before approval")
    for key in ("scenario", "boundary", "design_decisions", "determinism", "setup_safety",
                "readiness_command", "regression_command"):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            raise ValueError(f"missing acceptance design field: {key}")
    for key in ("allowed_files", "protected_files", "forbidden_files"):
        plan[key] = paths(plan.get(key), forbidden=key == "forbidden_files")
    if set(plan["allowed_files"]) & set(plan["forbidden_files"]):
        raise ValueError("allowed and forbidden files overlap")
    if plan["mode"] == "behavior":
        expected_tests(plan)
    elif not isinstance(plan.get("not_applicable_reason"), str) or not plan["not_applicable_reason"].strip():
        raise ValueError("preservation requires an explicit no-new-behavior justification")
    if path.as_posix() in plan["allowed_files"]:
        raise ValueError("support author cannot edit the approved plan")
    return plan


def expected_tests(plan):
    tests = plan.get("acceptance_tests")
    if not isinstance(tests, list) or not tests:
        raise ValueError("declare acceptance_tests and reapprove the behavior acceptance design")
    ids, commands = set(), set()
    for test in tests:
        if not isinstance(test, dict):
            raise ValueError("each acceptance test must be an object")
        for key in ("id", "scenario", "file", "command", "red_result_line",
                    "green_result_line", "expected_failure_marker"):
            value = test.get(key)
            if not isinstance(value, str) or not value.strip() or value != value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"acceptance test requires a nonempty single-line {key}")
        if test["file"] not in plan["protected_files"]:
            raise ValueError(f"expected acceptance test file must be protected: {test['file']}")
        if test["id"] in ids or test["command"] in commands:
            raise ValueError("acceptance test ids and focused commands must be unique")
        ids.add(test["id"])
        commands.add(test["command"])
        if test["command"] == plan["regression_command"]:
            raise ValueError("regression must explicitly exclude pending slice acceptance tests")
        if (test["red_result_line"] == test["green_result_line"]
                or any(test["id"] not in test[key] for key in ("red_result_line", "green_result_line"))):
            raise ValueError("acceptance test result lines must identify the test and distinguish red from green")
        code = test.get("expected_red_exit")
        if type(code) is not int or not 1 <= code <= 125:
            raise ValueError("expected_red_exit must identify an ordinary test failure")
    return tests


def save(path, state):
    path.write_text(json.dumps(state, indent=2) + "\n")


def prepare(plan_path):
    relative = plan_path.resolve().relative_to(Path.cwd().resolve())
    if (relative.parts[:2] != (".github", "plans") or len(relative.parts) != 4
            or relative.name != "02-slice-acceptance.json" or plan_path.is_symlink()):
        raise ValueError("acceptance plan must be .github/plans/<slice>/02-slice-acceptance.json")
    plan = read_plan(plan_path)
    state = {"plan_path": str(plan_path.resolve()), "plan": plan,
             "plan_hash": digest(plan_path), "snapshot_tree": snapshot.capture()["snapshot_tree"],
             "status": "support-authoring", "repository_root": str(Path.cwd().resolve())}
    with tempfile.NamedTemporaryFile(prefix="conductor-slice-acceptance-", suffix=".json", delete=False) as f:
        state_path = Path(f.name)
    save(state_path, state)
    return {"proceed": True, "state_path": str(state_path),
            "regression_command": plan["regression_command"], "summary": "Human-approved support scope frozen."}


def fingerprints(plan):
    # Recheck paths after authoring: newly created symlinks are not approved files.
    return {name: digest(name) for name in paths(plan["protected_files"])}


def check(state):
    if digest(state["plan_path"]) != state["plan_hash"]:
        raise ValueError("acceptance design changed; human design approval is required again")
    if state.get("status") != "approved":
        raise ValueError("human validation of built acceptance support is required before layers")
    if fingerprints(state["plan"]) != state["protected_hashes"]:
        raise ValueError("approved acceptance test/support changed; return to human acceptance review")


def run_checks(plan, final=False):
    evidence = []
    for field in ("readiness_command", "regression_command"):
        result = verification.run(plan[field], "slice-" + field, 2048)
        evidence.append(result)
        if result["exit_code"] != 0:
            return False, evidence, f"{field} failed: harness/regression failure is not expected red"
    if plan["mode"] == "behavior":
        for test in expected_tests(plan):
            for _ in range(1 if final else 2):
                result = verification.run(test["command"], "slice-acceptance", 2048)
                result.update(test_id=test["id"], scenario=test["scenario"], test_file=test["file"])
                evidence.append(result)
                log = Path(result["evidence_path"]).read_text()
                expected_line = test["green_result_line" if final else "red_result_line"]
                expected_exit = 0 if final else test["expected_red_exit"]
                if (result["exit_code"] != expected_exit or expected_line not in log.splitlines()
                        or (not final and test["expected_failure_marker"] not in log)):
                    phase = "green" if final else "behavioral red"
                    return False, evidence, f"acceptance test {test['id']} did not produce the approved {phase} evidence"
    return True, evidence, "Final acceptance is green." if final else "Support checks ready for human semantic validation."


def execute(action, state_path):
    state = json.loads(state_path.read_text())
    if str(Path.cwd().resolve()) != state["repository_root"]:
        raise ValueError("acceptance state belongs to another repository")
    plan = state["plan"]
    if plan["mode"] == "behavior":
        expected_tests(plan)
    if action == "verify":
        if digest(state["plan_path"]) != state["plan_hash"]:
            raise ValueError("approved acceptance plan was modified during support authoring")
        after = diff.current_tree()
        changed = diff.git(["diff", "--name-only", "--no-renames", state["snapshot_tree"], after]).splitlines()
        # Exact files only; persistence support uses this explicit scope, not test-author exceptions.
        paths(plan["allowed_files"])
        violations = sorted(set(changed) - set(plan["allowed_files"]))
        violations += sorted(set(changed) & set(plan["forbidden_files"]))
        if violations:
            raise ValueError("support boundary violations: " + ", ".join(sorted(set(violations))))
        protected = fingerprints(plan)
        patch_bytes = diff.git(["diff", "--binary", "--no-renames", state["snapshot_tree"], after], binary=True)
        with tempfile.NamedTemporaryFile(prefix="conductor-slice-support-", suffix=".patch", delete=False) as patch_file:
            patch_file.write(patch_bytes)
            patch_path = patch_file.name
        passed, evidence, summary = run_checks(plan)
        if (diff.current_tree() != after or digest(state["plan_path"]) != state["plan_hash"]
                or fingerprints(plan) != protected):
            passed, summary = False, "verification commands changed repository sources or the approved contract"
        state.update(status="needs-human-validation" if passed else "blocked",
                     changed_files=changed, protected_hashes=protected, evidence=evidence, patch_path=patch_path, verification_tree=after)
        save(state_path, state)
        return {"proceed": passed, "summary": summary, "changed_files": changed, "patch_path": patch_path, "evidence": evidence}
    if action == "approve":
        if state["status"] != "needs-human-validation":
            raise ValueError("support verification must pass before human acceptance approval")
        if (digest(state["plan_path"]) != state["plan_hash"]
                or fingerprints(plan) != state["protected_hashes"]
                or diff.current_tree() != state["verification_tree"]):
            raise ValueError("acceptance changed after verification; reprepare and review actual changes")
        state["status"] = "approved"
        save(state_path, state)
    check(state)
    if action == "final":
        passed, evidence, summary = run_checks(plan, final=True)
        # Commands must not mutate the protected contract either.
        check(state)
        return {"proceed": passed, "summary": summary, "evidence": evidence}
    return {"proceed": True, "summary": "Approved acceptance contract is unchanged."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify", "approve", "check", "final"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.path) if args.action == "prepare" else execute(args.action, args.path)
    except (ValueError, KeyError, OSError, TypeError, SystemExit) as error:
        result = {"proceed": False, "summary": str(error)}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
