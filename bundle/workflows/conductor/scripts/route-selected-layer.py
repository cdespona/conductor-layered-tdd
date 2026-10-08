#!/usr/bin/env python3
"""Resolve the selected layer and choose its deterministic workflow route."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


FRONTMATTER_VALUE = re.compile(
    r"(?m)^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.+?)\s*$"
)
WORK_KINDS = {"behavior", "cleanup"}


def fail(message: str) -> None:
    raise SystemExit(message)


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
    matches = [
        path
        for path in layers.glob("*.todo.md")
        if frontmatter_value(path.read_text(encoding="utf-8"), "selected_layer")
        == selected_layer
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        fail(f"multiple layer todos declare selected_layer {selected_layer}: {matches}")
    fail(f"selected layer todo does not exist under {layers}: {selected_layer}")


def classify(layer_map: Path) -> dict[str, str]:
    if not layer_map.is_file():
        fail(f"layer map does not exist: {layer_map}")
    selected_layer = frontmatter_value(layer_map.read_text(encoding="utf-8"), "selected_layer")
    if not selected_layer:
        fail(f"selected_layer is missing from {layer_map}")
    todo = selected_todo(layer_map, selected_layer)
    declared = frontmatter_value(todo.read_text(encoding="utf-8"), "work_kind")
    work_kind = declared or "behavior"
    if work_kind not in WORK_KINDS:
        fail(
            f"invalid work_kind in {todo}: {work_kind!r}; expected behavior or cleanup"
        )
    return {
        "artifact_path": str(layer_map),
        "selected_layer": selected_layer,
        "todo_path": str(todo),
        "work_kind": work_kind,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("layer_map", type=Path)
    args = parser.parse_args()
    print(json.dumps(classify(args.layer_map)))


if __name__ == "__main__":
    main()
