#!/usr/bin/env python3
"""Create a bounded patch from a pre-layer Git tree to the current tree."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


DEFAULT_PATCH_BYTES = 16_384
CODE_PATH = re.compile(r"`([^`]+)`")


def git(args: list[str], *, env: dict[str, str] | None = None, binary: bool = False):
    completed = subprocess.run(["git", *args], check=False, capture_output=True, env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.stdout if binary else completed.stdout.decode("utf-8", errors="replace").strip()


def current_tree() -> str:
    descriptor, index_name = tempfile.mkstemp(prefix="conductor-layer-current-", suffix=".index")
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


def boundary_paths(todo: Path) -> tuple[list[str], list[str]]:
    content = todo.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^## Implementation Boundary\s*\n(?P<body>.*?)(?=^##\s|\Z)", content)
    if not match:
        return [], []
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in match.group("body").splitlines()
        if line.lstrip().startswith("|")
    ]
    if len(rows) < 2:
        return [], []

    headers = [cell.lower() for cell in rows[0]]
    data_rows = [
        row
        for row in rows[1:]
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)
    ]
    allowed: list[str] = []
    forbidden: list[str] = []

    forbidden_columns = [
        index for index, header in enumerate(headers)
        if "forbid" in header or "read-only" in header or "readonly" in header
    ]
    allowed_columns = [
        index
        for index, header in enumerate(headers)
        if "allow" in header and "?" not in header
    ]
    if allowed_columns or forbidden_columns:
        for row in data_rows:
            for index in allowed_columns:
                if index < len(row):
                    allowed.extend(CODE_PATH.findall(row[index]))
            for index in forbidden_columns:
                if index < len(row):
                    forbidden.extend(CODE_PATH.findall(row[index]))
    else:
        status_index = next(
            (index for index, header in enumerate(headers) if "allow" in header),
            None,
        )
        path_index = next(
            (
                index
                for index, header in enumerate(headers)
                if "area" in header or "path" in header or "file" in header
            ),
            0,
        )
        for row in data_rows:
            if path_index >= len(row):
                continue
            paths = CODE_PATH.findall(row[path_index])
            status = row[status_index].lower() if status_index is not None and status_index < len(row) else ""
            target = forbidden if "forbid" in status or status in {"no", "read-only"} else allowed
            target.extend(paths)

    allowed = [path.strip().rstrip("/.,;") for path in allowed]
    forbidden = [path.strip().rstrip("/.,;") for path in forbidden]
    return sorted(set(allowed)), sorted(set(forbidden))


def within(path: str, boundary: str) -> bool:
    if any(character in boundary for character in "*?["):
        return fnmatch.fnmatchcase(path, boundary)
    candidate = PurePosixPath(path)
    base = PurePosixPath(boundary)
    return candidate == base or base in candidate.parents


def classify(
    changed: list[str], allowed: list[str], forbidden: list[str], exempt: list[str] | None = None
) -> dict[str, object]:
    exempt_paths = set(exempt or [])
    boundary_changes = [path for path in changed if path not in exempt_paths]
    forbidden_changes = [
        path for path in boundary_changes if any(within(path, item) for item in forbidden)
    ]
    outside = [
        path
        for path in boundary_changes
        if allowed and not any(within(path, item) for item in allowed)
    ]
    violations = sorted(set(forbidden_changes + outside))
    if not allowed and not forbidden:
        status = "unavailable"
    else:
        status = "violated" if violations else "passed"
    return {
        "boundary_status": status,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "boundary_exempt_paths": sorted(exempt_paths),
        "boundary_violations": violations,
    }


def create(
    snapshot: str, todo: Path | None, limit: int, allowed_paths: list[str] | None = None
) -> dict[str, object]:
    git(["cat-file", "-e", f"{snapshot}^{{tree}}"])
    repository_root = Path(git(["rev-parse", "--show-toplevel"])).resolve()
    todo_path = ""
    if todo is not None:
        try:
            todo_path = todo.resolve().relative_to(repository_root).as_posix()
        except ValueError:
            pass
    after = current_tree()
    names = git(["diff", "--name-only", "--find-renames", snapshot, after])
    changed = [line for line in names.splitlines() if line]
    patch = git(["diff", "--binary", "--find-renames", snapshot, after], binary=True)
    descriptor, patch_name = tempfile.mkstemp(prefix="conductor-layer-diff-", suffix=".patch")
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(patch)
    bounded = patch[:limit].decode("utf-8", errors="replace")
    allowed, forbidden = boundary_paths(todo) if todo is not None else (allowed_paths or [], [])
    result = {
        "snapshot_tree": snapshot,
        "current_tree": after,
        "changed_files": changed,
        "patch_path": patch_name,
        "patch_bytes": len(patch),
        "bounded_patch": bounded,
        "bounded_patch_bytes": len(bounded.encode("utf-8")),
        "patch_truncated": len(patch) > limit,
    }
    if todo is None:
        invalid = [
            path for path in allowed
            if not isinstance(path, str)
            or not path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or any(character in path for character in "*?[]")
            or (repository_root / path).is_dir()
            or (repository_root / path).is_symlink()
            or PurePosixPath(path).name == "AGENTS.md"
            or path == ".github/copilot-instructions.md"
            or path.startswith(".github/instructions/")
            or re.search(r"(^|/)(tests?|__tests__)/|(^|/)(test_[^/]+|[^/]+_test\.[^/]+|[^/]+\.(test|spec)\.[^/]+)$", path)
        ]
        violations = invalid + [path for path in changed if path not in allowed]
        result.update({
            "boundary_status": "passed" if allowed and len(allowed) <= 3 and not violations else "violated",
            "allowed_paths": allowed,
            "forbidden_paths": [],
            "boundary_violations": sorted(set(violations)),
        })
    else:
        result.update(classify(changed, allowed, forbidden, [todo_path] if todo_path else []))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-tree", required=True)
    boundary = parser.add_mutually_exclusive_group(required=True)
    boundary.add_argument("--todo", type=Path)
    boundary.add_argument("--allowed-paths-json")
    parser.add_argument("--max-patch-bytes", type=int, default=DEFAULT_PATCH_BYTES)
    args = parser.parse_args()
    if args.max_patch_bytes < 0:
        parser.error("--max-patch-bytes must be non-negative")
    allowed_paths = json.loads(args.allowed_paths_json) if args.allowed_paths_json is not None else None
    if allowed_paths is not None and (
        not isinstance(allowed_paths, list) or not all(isinstance(path, str) for path in allowed_paths)
    ):
        parser.error("--allowed-paths-json must contain an array of paths")
    print(json.dumps(create(args.snapshot_tree, args.todo, args.max_patch_bytes, allowed_paths)))


if __name__ == "__main__":
    main()
