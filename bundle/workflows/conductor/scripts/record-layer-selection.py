#!/usr/bin/env python3
"""Persist a human-selected layer in a layer-map frontmatter block."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


LAYER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def record_selection(artifact_path: Path, selected_layer: str) -> None:
    if artifact_path.name != "01-layer-map.md":
        fail(f"expected 01-layer-map.md, got: {artifact_path}")
    if not artifact_path.is_file():
        fail(f"layer map does not exist: {artifact_path}")
    if not LAYER_ID.fullmatch(selected_layer):
        fail(
            "selected layer must be one layer id or todo filename using only "
            "letters, numbers, dots, underscores, and hyphens"
        )

    content = artifact_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        fail(f"layer map has no YAML frontmatter: {artifact_path}")

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing_index is None:
        fail(f"layer map has unterminated YAML frontmatter: {artifact_path}")

    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    replacement = f"selected_layer: {json.dumps(selected_layer)}{newline}"
    selected_index = next(
        (
            index
            for index, line in enumerate(lines[1:closing_index], start=1)
            if re.match(r"^selected_layer\s*:", line)
        ),
        None,
    )
    if selected_index is None:
        lines.insert(closing_index, replacement)
    else:
        lines[selected_index] = replacement

    mode = artifact_path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=artifact_path.parent,
        prefix=f".{artifact_path.name}.",
        delete=False,
    ) as temporary:
        temporary.write("".join(lines))
        temporary_path = Path(temporary.name)

    try:
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, artifact_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: record-layer-selection.py <01-layer-map.md> <selected-layer>")

    artifact_path = Path(sys.argv[1])
    selected_layer = sys.argv[2].strip()
    record_selection(artifact_path, selected_layer)
    print(
        json.dumps(
            {
                "artifact_path": str(artifact_path),
                "selected_layer": selected_layer,
            }
        )
    )


if __name__ == "__main__":
    main()
