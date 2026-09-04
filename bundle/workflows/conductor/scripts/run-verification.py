#!/usr/bin/env python3
"""Run one verification command and emit bounded, structured evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


DEFAULT_EXCERPT_BYTES = 4096
FAILURE_LINE = re.compile(r"(?i)(fail(?:ed|ure)?|error|panic|exception|vulnerab|critical)")
GO_TEST = re.compile(r"^--- (PASS|FAIL):\s+([^\s(]+)", re.MULTILINE)
PYTEST_COUNTS = re.compile(r"(?P<count>\d+)\s+(?P<state>passed|failed|error)s?\b", re.I)


def utf8_prefix(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def bounded_excerpt(stdout: str, stderr: str, limit: int) -> tuple[str, bool]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    encoded = combined.encode("utf-8")
    if len(encoded) <= limit:
        return combined, False

    lines = combined.splitlines()
    failures = [line for line in lines if FAILURE_LINE.search(line)]
    selected = failures[:20]
    if not selected:
        selected = lines[-40:]
    excerpt = utf8_prefix("\n".join(selected), limit)
    return excerpt, True


def test_counts(text: str) -> tuple[int | None, int | None, list[str]]:
    go_results = GO_TEST.findall(text)
    if go_results:
        passed = sum(1 for state, _ in go_results if state == "PASS")
        failed_names = [name for state, name in go_results if state == "FAIL"]
        return passed, len(failed_names), failed_names[:50]

    counts: dict[str, int] = {}
    for match in PYTEST_COUNTS.finditer(text):
        counts[match.group("state").lower()] = int(match.group("count"))
    if counts:
        return counts.get("passed"), counts.get("failed", 0) + counts.get("error", 0), []
    return None, None, []


def run(command: str, kind: str, excerpt_bytes: int) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        ["sh", "-lc", command],
        check=False,
        capture_output=True,
    )
    duration_ms = round((time.monotonic() - started) * 1000)
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")

    descriptor, evidence_name = tempfile.mkstemp(prefix=f"conductor-{kind}-", suffix=".log")
    evidence_path = Path(evidence_name)
    with os.fdopen(descriptor, "wb") as evidence:
        evidence.write(b"===== STDOUT =====\n")
        evidence.write(completed.stdout)
        if completed.stdout and not completed.stdout.endswith(b"\n"):
            evidence.write(b"\n")
        evidence.write(b"===== STDERR =====\n")
        evidence.write(completed.stderr)
        if completed.stderr and not completed.stderr.endswith(b"\n"):
            evidence.write(b"\n")

    excerpt, truncated = bounded_excerpt(stdout, stderr, excerpt_bytes)
    passed, failed, failed_names = test_counts(stdout + "\n" + stderr)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "duration_ms": duration_ms,
        "passed_count": passed,
        "failed_count": failed,
        "failed_test_names": failed_names,
        "bounded_excerpt": excerpt,
        "evidence_path": str(evidence_path),
        "stdout_bytes": len(completed.stdout),
        "stderr_bytes": len(completed.stderr),
        "output_bytes": len(completed.stdout) + len(completed.stderr),
        "excerpt_bytes": len(excerpt.encode("utf-8")),
        "summary_truncated": truncated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--excerpt-bytes", type=int, default=DEFAULT_EXCERPT_BYTES)
    args = parser.parse_args()
    if args.excerpt_bytes < 0:
        parser.error("--excerpt-bytes must be non-negative")
    print(json.dumps(run(args.command, args.kind, args.excerpt_bytes)))


if __name__ == "__main__":
    main()
