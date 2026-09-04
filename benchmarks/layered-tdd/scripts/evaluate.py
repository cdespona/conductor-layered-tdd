#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ALLOWED_PREFIXES = (
    ".github/plans/",
    "internal/httpapi/",
    "internal/notification/",
    "internal/orders/",
)
FORBIDDEN_PREFIXES = (
    ".benchmark/",
    "internal/legacy/",
    "workflows/",
)
FORBIDDEN_FILES = {"AGENTS.md", "Makefile", "go.mod", "go.sum"}


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a materialized layered-TDD benchmark project.")
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    marker = project / ".benchmark" / "case.json"
    if not marker.is_file() or not (project / ".git").exists():
        parser.error(f"not a materialized benchmark project: {project}")
    case = json.loads(marker.read_text(encoding="utf-8"))
    run_kind = case.get("run_kind", "full")

    benchmark_dir = Path(__file__).resolve().parent.parent
    oracle_root = benchmark_dir / "oracle"
    if run_kind == "full":
        oracle_sources = sorted(oracle_root.rglob("*_test.go"))
        oracle_scope = "full"
    elif run_kind == "one-layer-diagnostic" and case.get("run_layers") == ["L10-domain"]:
        oracle_sources = [
            oracle_root / "internal" / "orders" / "domain_cancellation_oracle_test.go"
        ]
        oracle_scope = "L10-domain"
    else:
        parser.error(f"unsupported benchmark evaluation scope: {run_kind} {case.get('run_layers')}")

    copied: list[Path] = []
    try:
        for source in oracle_sources:
            relative = source.relative_to(oracle_root)
            target = project / relative
            if target.exists():
                raise RuntimeError(f"oracle target already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target)

        tests = run(["go", "test", "./..."], project)
    finally:
        for target in copied:
            target.unlink(missing_ok=True)

    changed = run(["git", "status", "--short", "--untracked-files=all"], project)
    changed_paths: list[str] = []
    for line in changed.stdout.splitlines():
        if len(line) >= 4:
            changed_paths.append(line[3:])

    boundary_violations = sorted(
        path
        for path in changed_paths
        if path in FORBIDDEN_FILES
        or path.startswith(FORBIDDEN_PREFIXES)
        or not path.startswith(ALLOWED_PREFIXES)
    )

    diff = run(["git", "diff", "--numstat", "HEAD"], project)
    additions = 0
    deletions = 0
    for line in diff.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            additions += int(parts[0])
            deletions += int(parts[1])
    for path in changed_paths:
        candidate = project / path
        if candidate.is_file() and run(["git", "ls-files", "--error-unmatch", "--", path], project).returncode != 0:
            additions += len(candidate.read_text(encoding="utf-8", errors="replace").splitlines())

    result = {
        "run_kind": run_kind,
        "oracle_scope": oracle_scope,
        "claim_eligible": bool(case.get("claim_eligible", run_kind == "full")),
        "oracle_passed": tests.returncode == 0,
        "boundary_passed": not boundary_violations,
        "boundary_violations": boundary_violations,
        "changed_paths": sorted(changed_paths),
        "git_additions": additions,
        "git_deletions": deletions,
        "test_exit_code": tests.returncode,
        "test_output_tail": "\n".join((tests.stdout + tests.stderr).splitlines()[-40:]),
    }
    output = project / ".benchmark" / "evaluation.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["oracle_passed"] and result["boundary_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
