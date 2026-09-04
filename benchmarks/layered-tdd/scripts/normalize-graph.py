#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser(description="Make a focused Graphify graph's source paths repository-relative.")
    parser.add_argument("graph", type=Path)
    parser.add_argument("prefix")
    args = parser.parse_args()

    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    prefix = PurePosixPath(args.prefix)
    changed = 0
    for collection in ("nodes", "links", "hyperedges"):
        for item in graph.get(collection, []):
            source = item.get("source_file")
            if not source:
                continue
            path = PurePosixPath(source)
            if path.parts[: len(prefix.parts)] == prefix.parts:
                continue
            item["source_file"] = str(prefix / path)
            changed += 1

    args.graph.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Normalized {changed} Graphify source paths with prefix {prefix}")


if __name__ == "__main__":
    main()
