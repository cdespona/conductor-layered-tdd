#!/usr/bin/env python3
"""Give every layer in the layer map its canonical todo file, layers/<layer id>.md.

Agents write the todo content but never decide its file name: this step renames
a todo written under another name and rewrites the Layer Matrix "Todo file"
column. It fails, before any human gate, when a layer has no todo it can match.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layer_matrix  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layer_map", type=Path)
    args = parser.parse_args()
    try:
        if not args.layer_map.is_file():
            raise ValueError(f"layer map does not exist: {args.layer_map}")
        result = layer_matrix.normalize(args.layer_map)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        print(json.dumps({"artifact_path": str(args.layer_map), "summary": str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
