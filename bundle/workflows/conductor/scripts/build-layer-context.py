#!/usr/bin/env python3
"""Build a bounded, deterministic context projection for one layer consumer."""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath


CONSUMER_BUDGETS = {
    "todo-generator": 12_000,
    "test-author": 16_000,
    "implementor": 20_000,
}
CODE_PATH = re.compile(r"`([^`]+)`")
FRONTMATTER_VALUE = re.compile(r"(?m)^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.+?)\s*$")
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs", ".scala",
    ".sh", ".sql", ".swift", ".ts", ".tsx",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def utf8_prefix(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def frontmatter_value(content: str, key: str) -> str:
    if not content.startswith("---"):
        return ""
    end = content.find("\n---", 3)
    if end < 0:
        return ""
    for match in FRONTMATTER_VALUE.finditer(content[3:end]):
        if match.group("key") == key:
            return match.group("value").strip().strip('"\'')
    return ""


def selected_todo(layer_map: Path, selected_layer: str) -> Path:
    layers = layer_map.parent / "layers"
    names = [selected_layer]
    if not selected_layer.endswith(".md"):
        names.extend([f"{selected_layer}.md", f"{selected_layer}.todo.md"])
    for name in names:
        candidate = layers / name
        if candidate.is_file():
            return candidate
    fail(f"selected layer todo does not exist under {layers}: {selected_layer}")


def markdown_rows(body: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in body.splitlines()
        if line.lstrip().startswith("|")
    ]


def clean_paths(values: list[str]) -> list[str]:
    paths = []
    for value in values:
        candidate = value.strip().rstrip("/.,;")
        if not candidate or candidate.startswith("<"):
            continue
        if "/" not in candidate and not any(character in candidate for character in "*?["):
            continue
        paths.append(candidate)
    return sorted(set(paths))


def boundary_paths(todo_content: str) -> tuple[list[str], list[str], list[str]]:
    match = re.search(
        r"(?ms)^## (?:Implementation )?Boundary\s*\n(?P<body>.*?)(?=^##\s|\Z)",
        todo_content,
    )
    if not match:
        return [], [], []
    rows = markdown_rows(match.group("body"))
    if len(rows) < 2:
        return [], [], []
    headers = [cell.lower() for cell in rows[0]]
    data_rows = [
        row for row in rows[1:]
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)
    ]
    allowed: list[str] = []
    forbidden: list[str] = []
    read_only: list[str] = []
    column_targets: dict[int, list[str]] = {}
    for index, header in enumerate(headers):
        if "forbid" in header:
            column_targets[index] = forbidden
        elif "read-only" in header or "readonly" in header:
            column_targets[index] = read_only
        elif "allow" in header and "?" not in header:
            column_targets[index] = allowed
    if column_targets:
        for row in data_rows:
            for index, target in column_targets.items():
                if index < len(row):
                    target.extend(CODE_PATH.findall(row[index]))
    else:
        status_index = next(
            (index for index, header in enumerate(headers) if "allow" in header),
            None,
        )
        path_index = next(
            (index for index, header in enumerate(headers) if any(word in header for word in ("area", "path", "file"))),
            0,
        )
        for row in data_rows:
            if path_index >= len(row):
                continue
            status = row[status_index].lower() if status_index is not None and status_index < len(row) else ""
            target = forbidden if "forbid" in status or status == "no" else read_only if "read-only" in status else allowed
            target.extend(CODE_PATH.findall(row[path_index]))
    return clean_paths(allowed), clean_paths(forbidden), clean_paths(read_only)


def within(path: str, boundary: str) -> bool:
    if any(character in boundary for character in "*?["):
        return fnmatch.fnmatchcase(path, boundary)
    candidate = PurePosixPath(path)
    base = PurePosixPath(boundary)
    return candidate == base or base in candidate.parents


def expand_paths(root: Path, declared: list[str], limit: int = 40) -> list[str]:
    found: list[str] = []
    for item in declared:
        if len(found) >= limit:
            break
        matches: list[Path]
        if any(character in item for character in "*?["):
            matches = [Path(path) for path in glob.glob(str(root / item), recursive=True)]
        else:
            candidate = root / item
            if candidate.is_dir():
                matches = [path for path in candidate.rglob("*") if path.is_file()]
            else:
                matches = [candidate]
        for path in sorted(matches):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            relative = path.resolve().relative_to(root).as_posix()
            if relative not in found:
                found.append(relative)
            if len(found) >= limit:
                break
    return found


def is_test_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name.endswith("_test.go")
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or "/test/" in f"/{path.lower()}/"
        or "/tests/" in f"/{path.lower()}/"
    )


def declared_test_paths(paths: list[str]) -> list[str]:
    return sorted({path for path in paths if is_test_path(path) or "test" in path.lower()})


def source_projection(root: Path, files: list[str], budget: int) -> tuple[str, list[dict[str, object]], bool]:
    chunks: list[str] = []
    records: list[dict[str, object]] = []
    remaining = budget
    truncated_any = False
    for relative in files:
        if remaining <= 0:
            truncated_any = True
            break
        path = root / relative
        raw = path.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        header = f"### {relative}\n"
        content_budget = min(5_000, max(0, remaining - len(header.encode("utf-8"))))
        excerpt, truncated = utf8_prefix(content, content_budget)
        chunk = f"{header}{excerpt.rstrip()}\n"
        chunks.append(chunk)
        used = len(chunk.encode("utf-8"))
        remaining -= used
        truncated_any = truncated_any or truncated
        records.append(
            {
                "path": relative,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "excerpt_bytes": len(excerpt.encode("utf-8")),
                "truncated": truncated,
            }
        )
    projection, final_truncated = utf8_prefix("\n".join(chunks), budget)
    return projection, records, truncated_any or final_truncated


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        json.dump(value, temporary, indent=2, ensure_ascii=False)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build(
    consumer: str,
    layer_map: Path | None,
    todo: Path | None,
    request: str = "",
) -> dict[str, object]:
    root = Path.cwd().resolve()
    if todo is None:
        if layer_map is None or not layer_map.is_file():
            fail("--map must point to 01-layer-map.md when --todo is omitted")
        map_content = layer_map.read_text(encoding="utf-8")
        selected_layer = frontmatter_value(map_content, "selected_layer")
        if not selected_layer:
            fail(f"selected_layer is missing from {layer_map}")
        todo = selected_todo(layer_map, selected_layer)
    else:
        if not todo.is_file():
            fail(f"layer todo does not exist: {todo}")
        selected_layer = frontmatter_value(todo.read_text(encoding="utf-8"), "selected_layer")
        if not selected_layer:
            fail(f"selected_layer is missing from {todo}")
        if layer_map is None:
            layer_map = todo.parent.parent / "01-layer-map.md"
        if not layer_map.is_file():
            fail(f"layer map does not exist: {layer_map}")
        map_content = layer_map.read_text(encoding="utf-8")

    todo_content = todo.read_text(encoding="utf-8")
    allowed, forbidden, read_only = boundary_paths(todo_content)
    existing_allowed = expand_paths(root, allowed)
    existing_read_only = expand_paths(root, read_only)
    source_files = []
    for path in existing_allowed + existing_read_only:
        if path not in source_files and (
            path in existing_read_only or not any(within(path, item) for item in forbidden)
        ):
            source_files.append(path)
    production_files = sorted(path for path in source_files if not is_test_path(path))
    test_files = sorted(
        set(path for path in source_files if is_test_path(path))
        | set(declared_test_paths(allowed + read_only))
    )
    source_context, sources, source_truncated = source_projection(
        root, source_files, CONSUMER_BUDGETS[consumer]
    )
    map_excerpt, map_truncated = utf8_prefix(map_content, 8_000)
    contract_excerpt, contract_truncated = utf8_prefix(todo_content, 14_000)
    request_excerpt, request_truncated = utf8_prefix(request, 12_000)
    relative_map = layer_map.resolve().relative_to(root).as_posix()
    relative_todo = todo.resolve().relative_to(root).as_posix()
    manifest_path = todo.parent.parent / ".context" / f"{selected_layer}-{consumer}.json"
    manifest = {
        "schema_version": 1,
        "consumer": consumer,
        "selected_layer": selected_layer,
        "layer_map_path": relative_map,
        "todo_path": relative_todo,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "read_only_paths": read_only,
        "production_files": production_files,
        "test_files": test_files,
        "source_files": sources,
        "source_context": source_context,
        "source_context_bytes": len(source_context.encode("utf-8")),
        "source_context_truncated": source_truncated,
        "layer_map_excerpt": map_excerpt,
        "layer_map_excerpt_truncated": map_truncated,
        "layer_contract": contract_excerpt,
        "layer_contract_truncated": contract_truncated,
        "request_contract": request_excerpt,
        "request_contract_truncated": request_truncated,
    }
    fingerprint_source = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    manifest["source_fingerprint"] = hashlib.sha256(fingerprint_source).hexdigest()
    atomic_json(manifest_path, manifest)
    output = dict(manifest)
    output["manifest_path"] = manifest_path.resolve().relative_to(root).as_posix()
    output["manifest_bytes"] = manifest_path.stat().st_size
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", required=True, choices=sorted(CONSUMER_BUDGETS))
    parser.add_argument("--map", type=Path)
    parser.add_argument("--todo", type=Path)
    parser.add_argument("--request", default="")
    args = parser.parse_args()
    if args.map is None and args.todo is None:
        parser.error("one of --map or --todo is required")
    print(json.dumps(build(args.consumer, args.map, args.todo, args.request), ensure_ascii=False))


if __name__ == "__main__":
    main()
