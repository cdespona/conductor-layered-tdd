#!/usr/bin/env python3
"""Deterministically prepare and finalize an approved test-author handoff."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def fail(message: str) -> None:
    raise SystemExit(message)


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def replace_frontmatter(content: str, values: dict[str, str]) -> str:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        fail("layer todo has no YAML frontmatter")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        fail("layer todo has unterminated YAML frontmatter")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    for key, value in values.items():
        replacement = f"{key}: {value}{newline}"
        index = next(
            (current for current, line in enumerate(lines[1:closing], 1)
             if re.match(rf"^{re.escape(key)}\s*:", line)),
            None,
        )
        if index is None:
            lines.insert(closing, replacement)
            closing += 1
        else:
            lines[index] = replacement
    return "".join(lines)


def append_decision(content: str, feedback: str) -> str:
    pattern = re.compile(r"(?ms)^## Decision Log\s*\n(?P<body>.*?)(?=^##\s|\Z)")
    match = pattern.search(content)
    if not match or "| Decision |" not in match.group("body"):
        fail("layer todo is missing the required Decision Log table")
    body = match.group("body").rstrip()
    row = "| {} | {} | {} | {} |".format(
        "Approved agent-authored top-level test",
        markdown_cell(feedback),
        "layer todo gate: author_agent_test",
        datetime.now(UTC).isoformat(),
    )
    replacement = f"## Decision Log\n{body}\n{row}\n\n"
    return content[: match.start()] + replacement + content[match.end() :]


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def task_rows(content: str) -> tuple[re.Match[str], list[str], list[int], list[str]]:
    pattern = re.compile(r"(?ms)^## Task Board\s*\n(?P<body>.*?)(?=^##\s|\Z)")
    match = pattern.search(content)
    if not match:
        return match, [], [], ["layer todo is missing required section: ## Task Board"]
    lines = match.group("body").strip("\n").splitlines()
    table_indexes = [index for index, line in enumerate(lines) if line.lstrip().startswith("|")]
    if len(table_indexes) < 3:
        return match, lines, [], ["Task Board table is missing or empty"]
    header_index = table_indexes[0]
    headers = [cell.lower() for cell in table_cells(lines[header_index])]
    required = {"task", "type", "owner", "status"}
    if not required.issubset(headers):
        return match, lines, [], ["Task Board requires Task, Type, Owner, and Status columns"]
    type_index = headers.index("type")
    exact: list[int] = []
    fallback: list[int] = []
    for index in table_indexes[2:]:
        cells = table_cells(lines[index])
        if len(cells) <= type_index:
            continue
        kind = cells[type_index].strip("`").lower().replace("_", "-")
        if kind in {"top-level-test", "top-level test"}:
            exact.append(index)
        elif kind == "test":
            fallback.append(index)
    selected = exact if exact else fallback if len(fallback) == 1 else []
    errors = [] if selected else [
        "Task Board needs at least one Type=top-level-test row; "
        "legacy Type=test is accepted only when exactly one exists"
    ]
    return match, lines, selected, errors


def update_task_rows(content: str, status: str) -> tuple[str, list[str]]:
    match, lines, selected, errors = task_rows(content)
    if errors:
        return content, errors
    headers = [cell.lower() for cell in table_cells(next(line for line in lines if line.lstrip().startswith("|")))]
    task_index, owner_index, status_index = (
        headers.index("task"), headers.index("owner"), headers.index("status")
    )
    for index in selected:
        cells = table_cells(lines[index])
        cells[owner_index] = "agent"
        cells[status_index] = status
        checked = "x" if status == "done" else " "
        cells[task_index] = re.sub(r"^-\s*\[[ xX]\]", f"- [{checked}]", cells[task_index])
        lines[index] = "| " + " | ".join(cells) + " |"
    replacement = "## Task Board\n\n" + "\n".join(lines).rstrip() + "\n\n"
    return content[: match.start()] + replacement + content[match.end() :], []


def prepare(todo: Path, feedback: str) -> dict[str, object]:
    if not todo.is_file():
        fail(f"layer todo does not exist: {todo}")
    content = todo.read_text(encoding="utf-8")
    content, errors = update_task_rows(content, "in-progress")
    if errors:
        return {"proceed": False, "artifact_path": str(todo), "errors": errors, "summary": errors[0]}
    content = replace_frontmatter(content, {
        "status": "needs-human-test-gate",
        "owner": "human",
        "test_ownership": "agent-written-after-approval",
        "red_gate_state": "blocked",
    })
    content = append_decision(content, feedback)
    atomic_write(todo, content)
    return {
        "proceed": True,
        "artifact_path": str(todo),
        "errors": [],
        "summary": "recorded test-author approval and marked top-level test tasks in progress",
    }


def git(args: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(["git", *args], check=False, capture_output=True, text=True, env=env)
    if completed.returncode != 0:
        fail(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def current_tree() -> str:
    descriptor, index_name = tempfile.mkstemp(prefix="conductor-test-author-", suffix=".index")
    os.close(descriptor)
    Path(index_name).unlink()
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = index_name
    try:
        git(["read-tree", "HEAD"], env=environment)
        git(["add", "-A"], env=environment)
        return git(["write-tree"], env=environment)
    finally:
        Path(index_name).unlink(missing_ok=True)


def within(path: str, boundary: str) -> bool:
    if any(character in boundary for character in "*?["):
        return fnmatch.fnmatchcase(path, boundary)
    candidate, base = PurePosixPath(path), PurePosixPath(boundary)
    return candidate == base or base in candidate.parents


def is_test_path(path: str) -> bool:
    lowered, name = path.lower(), PurePosixPath(path).name.lower()
    return (
        name.endswith("_test.go") or name.startswith("test_") or ".test." in name
        or ".spec." in name or "/test/" in f"/{lowered}/" or "/tests/" in f"/{lowered}/"
    )


def complete(
    todo: Path, manifest: Path, snapshot: str, reported_json: str,
    checkpoint_required: bool = False,
) -> dict[str, object]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    reported = sorted(set(json.loads(reported_json)))
    after = current_tree()
    changed = sorted(line for line in git(["diff", "--name-only", snapshot, after]).splitlines() if line)
    allowed = data.get("allowed_paths", [])
    forbidden = data.get("forbidden_paths", [])
    read_only = data.get("read_only_paths", [])
    if checkpoint_required:
        errors = [] if not changed else [
            "test author requested a checkpoint after changing files: " + ", ".join(changed)
        ]
        return {
            "proceed": False, "checkpoint_required": not errors,
            "artifact_path": str(todo), "changed_files": changed,
            "violations": changed, "errors": errors,
            "summary": "checkpoint verified with no file changes" if not errors else errors[0],
        }
    violations = [
        path for path in changed
        if not is_test_path(path)
        or not any(within(path, item) for item in allowed)
        or any(within(path, item) for item in forbidden + read_only)
    ]
    errors: list[str] = []
    if not changed:
        errors.append("test author changed no test files")
    if violations:
        errors.append("non-test, forbidden, read-only, or out-of-boundary changes: " + ", ".join(violations))
    if reported != changed:
        errors.append(f"reported test files {reported} do not match changed files {changed}")
    if errors:
        return {
            "proceed": False, "checkpoint_required": False,
            "artifact_path": str(todo), "changed_files": changed,
            "violations": violations, "errors": errors, "summary": "; ".join(errors),
        }
    content, task_errors = update_task_rows(todo.read_text(encoding="utf-8"), "done")
    if task_errors:
        return {
            "proceed": False, "checkpoint_required": False,
            "artifact_path": str(todo), "changed_files": changed,
            "violations": [], "errors": task_errors, "summary": task_errors[0],
        }
    content = replace_frontmatter(content, {
        "status": "needs-human-test-gate", "owner": "human",
        "test_ownership": "agent-written-after-approval", "red_gate_state": "blocked",
    })
    atomic_write(todo, content)
    return {
        "proceed": True, "checkpoint_required": False,
        "artifact_path": str(todo), "changed_files": changed,
        "violations": [], "errors": [],
        "summary": "validated test-only changes and marked top-level test tasks done",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    before = subparsers.add_parser("prepare")
    before.add_argument("--artifact", required=True, type=Path)
    before.add_argument("--feedback", default="")
    after = subparsers.add_parser("complete")
    after.add_argument("--artifact", required=True, type=Path)
    after.add_argument("--manifest", required=True, type=Path)
    after.add_argument("--snapshot-tree", required=True)
    after.add_argument("--reported-files", required=True)
    after.add_argument("--checkpoint-required", choices=["true", "false"], default="false")
    args = parser.parse_args()
    result = prepare(args.artifact, args.feedback) if args.operation == "prepare" else complete(
        args.artifact, args.manifest, args.snapshot_tree, args.reported_files,
        args.checkpoint_required == "true",
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
