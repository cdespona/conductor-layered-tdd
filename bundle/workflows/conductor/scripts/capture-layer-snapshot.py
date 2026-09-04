#!/usr/bin/env python3
"""Capture the complete current working tree as an immutable Git tree."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def git(args: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(["git", *args], check=False, capture_output=True, text=True, env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def capture() -> dict[str, object]:
    root = Path(git(["rev-parse", "--show-toplevel"]))
    descriptor, index_name = tempfile.mkstemp(prefix="conductor-layer-snapshot-", suffix=".index")
    os.close(descriptor)
    Path(index_name).unlink()
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = index_name
    try:
        git(["read-tree", "HEAD"], env=environment)
        git(["add", "-A"], env=environment)
        tree = git(["write-tree"], env=environment)
    finally:
        Path(index_name).unlink(missing_ok=True)
    return {"repository_root": str(root), "snapshot_tree": tree}


if __name__ == "__main__":
    print(json.dumps(capture()))
