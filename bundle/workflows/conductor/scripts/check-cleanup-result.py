#!/usr/bin/env python3
"""Deterministically check declared cleanup removals and remaining references."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path


SKIP_PARTS = {".git", ".context", "graphify-out", "node_modules", "vendor"}
TEXT_SUFFIXES = {
    ".c", ".cc", ".conf", ".cpp", ".cs", ".go", ".gradle", ".h", ".hpp",
    ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".php", ".properties",
    ".py", ".rb", ".rs", ".scala", ".sh", ".sql", ".swift", ".toml", ".ts",
    ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
REQUIRED_HEADINGS = {
    "Cleanup Contract", "Supersession Chain", "Removal Inventory",
    "Preservation Contract", "Remaining Reference Checks", "Implementation Boundary",
    "Task Board", "Risk Board", "Decision Log",
}


def section(content: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(?P<body>.*?)(?=^##\s|\Z)", content
    )
    return match.group("body") if match else ""


def table(body: str) -> tuple[list[str], list[list[str]]]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in body.splitlines()
        if line.lstrip().startswith("|")
    ]
    if len(rows) < 2:
        return [], []
    headers = [cell.lower() for cell in rows[0]]
    data = [
        row for row in rows[1:]
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)
    ]
    return headers, data


def cell_value(cell: str) -> str:
    value = cell.strip().strip("`").strip()
    return "" if value in {"-", "none", "n/a"} else value


def frontmatter_value(content: str, key: str) -> str:
    if not content.startswith("---"):
        return ""
    end = content.find("\n---", 3)
    if end < 0:
        return ""
    match = re.search(rf"(?m)^{re.escape(key)}\s*:\s*(.+?)\s*$", content[3:end])
    return match.group(1).strip().strip('"\'') if match else ""


def safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def removal_targets(content: str) -> list[str]:
    headers, rows = table(section(content, "Removal Inventory"))
    if "target" not in headers:
        raise ValueError("Removal Inventory must contain a Target column")
    target_index = headers.index("target")
    targets = [cell_value(row[target_index]) for row in rows if target_index < len(row)]
    return [target for target in targets if target]


def reference_checks(content: str) -> list[tuple[str, str]]:
    headers, rows = table(section(content, "Remaining Reference Checks"))
    if not headers:
        raise ValueError("Remaining Reference Checks table is required")
    if "pattern" not in headers or "root" not in headers:
        raise ValueError("Remaining Reference Checks must contain Pattern and Root columns")
    pattern_index, root_index = headers.index("pattern"), headers.index("root")
    checks = []
    for row in rows:
        if max(pattern_index, root_index) >= len(row):
            continue
        pattern, root = cell_value(row[pattern_index]), cell_value(row[root_index])
        if pattern and root:
            checks.append((pattern, root))
    if not checks:
        raise ValueError("at least one remaining-reference check is required")
    return checks


def matching_paths(root: Path, target: str) -> list[str]:
    if any(character in target for character in "*?["):
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and fnmatch.fnmatchcase(path.relative_to(root).as_posix(), target)
        )
    candidate = root / target
    if candidate.is_file():
        return [target]
    if candidate.is_dir():
        return [f"{target}/"]
    return []


def searchable_files(root: Path, search_root: str, todo: Path):
    base = (root / search_root).resolve()
    try:
        base.relative_to(root)
    except ValueError as error:
        raise ValueError(f"reference root escapes repository: {search_root}") from error
    if not base.exists():
        raise ValueError(f"reference root does not exist: {search_root}")
    candidates = [base] if base.is_file() else base.rglob("*") if base.is_dir() else []
    for path in candidates:
        if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(root).parts):
            continue
        if path.resolve() == todo.resolve() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def validate_plan(todo: Path, root: Path) -> dict[str, object]:
    root = root.resolve()
    todo = todo.resolve()
    content = todo.read_text(encoding="utf-8")
    errors: list[str] = []
    if frontmatter_value(content, "work_kind") != "cleanup":
        errors.append("frontmatter work_kind must be cleanup")
    for heading in sorted(REQUIRED_HEADINGS):
        if not section(content, heading):
            errors.append(f"missing or empty section: {heading}")
    try:
        targets = removal_targets(content)
        if not targets:
            errors.append("Removal Inventory has no targets")
        errors.extend(
            f"removal target must be repository-relative: {target}"
            for target in targets if not safe_relative(target)
        )
    except ValueError as error:
        targets = []
        errors.append(str(error))
    try:
        checks = reference_checks(content)
    except ValueError as error:
        checks = []
        errors.append(str(error))

    errors.extend(
        f"reference root must be repository-relative: {search_root}"
        for _, search_root in checks if not safe_relative(search_root)
    )
    errors.extend(
        f"reference root does not exist: {search_root}"
        for _, search_root in checks
        if safe_relative(search_root) and not (root / search_root).exists()
    )
    return {
        "passed": not errors,
        "todo_path": todo.relative_to(root).as_posix(),
        "declared_removal_targets": targets,
        "reference_check_count": len(checks),
        "errors": errors,
        "summary": "cleanup plan is structurally valid" if not errors else "cleanup plan is structurally invalid",
    }


def check(todo: Path, root: Path) -> dict[str, object]:
    root = root.resolve()
    todo = todo.resolve()
    plan = validate_plan(todo, root)
    content = todo.read_text(encoding="utf-8")
    errors = list(plan["errors"])
    targets = list(plan["declared_removal_targets"])
    try:
        checks = reference_checks(content)
    except ValueError:
        checks = []

    remaining_targets = [item for target in targets for item in matching_paths(root, target)]
    remaining_references: list[str] = []
    for pattern, search_root in checks:
        try:
            paths = list(searchable_files(root, search_root, todo))
        except ValueError as error:
            errors.append(str(error))
            continue
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    relative = path.relative_to(root).as_posix()
                    remaining_references.append(f"{relative}:{number}:{pattern}")
                    if len(remaining_references) >= 50:
                        break
            if len(remaining_references) >= 50:
                break

    passed = not errors and not remaining_targets and not remaining_references
    return {
        "passed": passed,
        "todo_path": todo.relative_to(root).as_posix(),
        "declared_removal_targets": targets,
        "remaining_targets": remaining_targets,
        "reference_check_count": len(checks),
        "remaining_references": remaining_references,
        "errors": errors,
        "summary": "cleanup checks passed" if passed else "cleanup checks require review",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--todo", required=True, type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    result = validate_plan(args.todo.resolve(), root) if args.plan_only else check(args.todo.resolve(), root)
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
