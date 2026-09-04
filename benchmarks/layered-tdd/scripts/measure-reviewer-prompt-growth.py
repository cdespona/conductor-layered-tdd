#!/usr/bin/env python3
"""Compare raw verification-log scaling in control and candidate reviewer prompts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


RAW_VERIFICATION_STREAM = re.compile(
    r"{{\s*verification_(?:tests|lint|security)\.output\.(?:stdout|stderr)[^}]*}}"
)
DEFAULT_SIZES = (1_024, 51_200, 512_000)


def parse_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as error:
        raise argparse.ArgumentTypeError("sizes must be comma-separated integers") from error
    if len(sizes) < 2 or sizes[0] < 0:
        raise argparse.ArgumentTypeError("provide at least two non-negative sizes")
    return sizes


def measure_prompt(path: Path, sizes: tuple[int, ...]) -> dict[str, object]:
    template = path.read_text(encoding="utf-8")
    references = len(RAW_VERIFICATION_STREAM.findall(template))
    samples = []
    for stream_bytes in sizes:
        payload = "x" * stream_bytes
        rendered = RAW_VERIFICATION_STREAM.sub(payload, template)
        samples.append(
            {
                "stream_bytes": stream_bytes,
                "rendered_prompt_bytes": len(rendered.encode("utf-8")),
            }
        )
    growth = samples[-1]["rendered_prompt_bytes"] - samples[0]["rendered_prompt_bytes"]
    return {
        "path": str(path.resolve()),
        "raw_stream_references": references,
        "samples": samples,
        "growth_bytes": growth,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Zero-model structural check that complete verification streams no longer "
            "make the layer-reviewer prompt grow with raw log volume."
        )
    )
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument(
        "--sizes",
        type=parse_sizes,
        default=DEFAULT_SIZES,
        help="Synthetic bytes per verification stream (default: 1024,51200,512000).",
    )
    parser.add_argument(
        "--max-candidate-growth-bytes",
        type=int,
        default=4_096,
        help="Maximum permitted candidate prompt growth across the synthetic range.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.max_candidate_growth_bytes < 0:
        parser.error("--max-candidate-growth-bytes must be non-negative")
    for path in (args.control, args.candidate):
        if not path.is_file():
            parser.error(f"reviewer prompt does not exist: {path}")

    control = measure_prompt(args.control, args.sizes)
    candidate = measure_prompt(args.candidate, args.sizes)
    control_has_raw_streams = int(control["raw_stream_references"]) > 0
    candidate_removed_raw_streams = int(candidate["raw_stream_references"]) == 0
    candidate_growth_bounded = (
        int(candidate["growth_bytes"]) <= args.max_candidate_growth_bytes
    )
    passed = (
        control_has_raw_streams
        and candidate_removed_raw_streams
        and candidate_growth_bounded
    )
    result = {
        "check": "layer-reviewer-raw-verification-growth",
        "sizes_are_bytes_per_stream": True,
        "control": control,
        "candidate": candidate,
        "max_candidate_growth_bytes": args.max_candidate_growth_bytes,
        "assertions": {
            "control_has_raw_streams": control_has_raw_streams,
            "candidate_removed_raw_streams": candidate_removed_raw_streams,
            "candidate_growth_bounded": candidate_growth_bounded,
        },
        "passed": passed,
        "claim_scope": (
            "Structural prompt-scaling proof only; model quality and end-to-end behavior "
            "still require diagnostic and full benchmark promotion."
        ),
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
