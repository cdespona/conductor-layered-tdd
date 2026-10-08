#!/usr/bin/env python3
"""Apply deterministic red-suite evidence and human decisions to a layer todo."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(message)


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def replace_frontmatter(content: str, values: dict[str, str]) -> str:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        fail("layer todo has no YAML frontmatter")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        fail("layer todo has unterminated YAML frontmatter")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    for key, value in values.items():
        replacement = f"{key}: {value}{newline}"
        index = next(
            (
                current
                for current, line in enumerate(lines[1:closing], 1)
                if re.match(rf"^{re.escape(key)}\s*:", line)
            ),
            None,
        )
        if index is None:
            lines.insert(closing, replacement)
            closing += 1
        else:
            lines[index] = replacement
    return "".join(lines)


def upsert_section(content: str, heading: str, body: str) -> str:
    pattern = re.compile(
        rf"(?ms)^{re.escape(heading)}\s*\n.*?(?=^##\s|\Z)"
    )
    replacement = f"{heading}\n\n{body.rstrip()}\n\n"
    if not pattern.search(content):
        return f"{content.rstrip()}\n\n{replacement}"
    return pattern.sub(replacement, content, count=1)


def append_decision(content: str, row: str) -> str:
    pattern = re.compile(r"(?ms)^## Decision Log\s*\n(?P<body>.*?)(?=^##\s|\Z)")
    match = pattern.search(content)
    if not match:
        fail("layer todo is missing required section: ## Decision Log")
    body = match.group("body").rstrip()
    if "| Decision |" not in body:
        fail("Decision Log table header is missing")
    replacement = f"## Decision Log\n{body}\n{row}\n\n"
    return content[: match.start()] + replacement + content[match.end() :]


def update_red_gate_row(content: str, row: str) -> str:
    section = re.search(r"(?ms)^## Red-Test Gate\s*\n(?P<body>.*?)(?=^##\s|\Z)", content)
    if not section:
        return content
    body = section.group("body")
    rows = list(re.finditer(r"(?m)^\|.*\|[ \t]*$", body))
    if len(rows) != 3 or "| State |" not in rows[0].group():
        fail("Red-Test Gate must contain one state row")
    data = rows[2]
    return content[: section.start("body") + data.start()] + row + content[section.start("body") + data.end() :]


def atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_todo(path: Path) -> str:
    if not path.is_file():
        fail(f"layer todo does not exist: {path}")
    return path.read_text(encoding="utf-8")


def record_evidence(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.artifact)
    content = replace_frontmatter(
        read_todo(path),
        {"status": "needs-human-test-gate", "owner": "human", "red_gate_state": "blocked"},
    )
    observed = "green" if args.exit_code == 0 else "non-zero"
    body = "\n".join(
        [
            "| Check | Command | Exit code | Result | Evidence path | Output bytes | Excerpt |",
            "| --- | --- | ---: | --- | --- | ---: | --- |",
            "| Full suite | `{}` | {} | {} | `{}` | {} | {} |".format(
                markdown_cell(args.command),
                args.exit_code,
                observed,
                markdown_cell(args.evidence_path),
                args.output_bytes,
                markdown_cell(args.excerpt) or "(no output)",
            ),
        ]
    )
    content = upsert_section(content, "## Evidence", body)
    content = update_red_gate_row(
        content,
        f"| `blocked` | `{markdown_cell(args.command)}` | Exit {args.exit_code} ({observed}); see Evidence. | Awaiting human red-gate decision. | No |",
    )
    atomic_write(path, content)
    summary = (
        f"{args.command} exited {args.exit_code} ({observed}); "
        f"{args.output_bytes} output bytes; evidence: {args.evidence_path}"
    )
    return {"artifact_path": str(path), "summary": summary}


def record_decision(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.artifact)
    content = read_todo(path)
    valid = (args.selection == "confirm_red" and args.exit_code != 0) or (
        args.selection == "accept_green" and args.exit_code == 0
    )
    if args.selection not in {"confirm_red", "accept_green"}:
        fail(f"unsupported red-gate selection: {args.selection}")

    if valid:
        red_state = "observed-red" if args.selection == "confirm_red" else "already-passing-human-approved"
        content = replace_frontmatter(
            content,
            {"status": "ready-for-implementation", "owner": "agent", "red_gate_state": red_state},
        )
        result = "approved"
    else:
        red_state = "blocked"
        content = replace_frontmatter(
            content,
            {"status": "needs-human-test-gate", "owner": "human", "red_gate_state": red_state},
        )
        result = "blocked: selection contradicts exit code"

    evidence = f"{args.command} (exit {args.exit_code}); {args.evidence_path}; {result}"
    content = update_red_gate_row(
        content,
        f"| `{red_state}` | `{markdown_cell(args.command)}` | Exit {args.exit_code}; see Evidence. | {markdown_cell(args.feedback) or result} | {'Yes' if valid else 'No'} |",
    )
    row = "| {} | {} | {} | {} |".format(
        markdown_cell(args.selection),
        markdown_cell(args.feedback),
        markdown_cell(evidence),
        datetime.now(UTC).isoformat(),
    )
    content = append_decision(content, row)
    atomic_write(path, content)
    return {
        "proceed": valid,
        "summary": f"{args.selection}: {result}; red_gate_state={red_state}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)

    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("--artifact", required=True)
    evidence.add_argument("--command", required=True)
    evidence.add_argument("--exit-code", required=True, type=int)
    evidence.add_argument("--evidence-path", required=True)
    evidence.add_argument("--output-bytes", required=True, type=int)
    evidence.add_argument("--excerpt", default="")

    decision = subparsers.add_parser("decision")
    decision.add_argument("--artifact", required=True)
    decision.add_argument("--selection", required=True)
    decision.add_argument("--feedback", default="")
    decision.add_argument("--command", required=True)
    decision.add_argument("--exit-code", required=True, type=int)
    decision.add_argument("--evidence-path", required=True)

    args = parser.parse_args()
    result = record_evidence(args) if args.operation == "evidence" else record_decision(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
