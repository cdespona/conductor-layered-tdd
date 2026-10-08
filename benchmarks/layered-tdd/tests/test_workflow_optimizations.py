from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPOSITORY_ROOT / "bundle" / "workflows" / "conductor"
SCRIPTS = WORKFLOW_ROOT / "scripts"
WORKFLOW = WORKFLOW_ROOT / "layered-tdd.yaml"
REVIEWER = WORKFLOW_ROOT / "prompts" / "layer-reviewer.md"
TODO_GENERATOR = WORKFLOW_ROOT / "prompts" / "layer-todo-generator.md"
TEST_AUTHOR = WORKFLOW_ROOT / "prompts" / "agent-test-author.md"
IMPLEMENTOR = WORKFLOW_ROOT / "prompts" / "implementor.md"
CLEANUP_PLANNER = WORKFLOW_ROOT / "prompts" / "cleanup-planner.md"
CLEANUP_IMPLEMENTOR = WORKFLOW_ROOT / "prompts" / "cleanup-implementor.md"
CLEANUP_REVIEWER = WORKFLOW_ROOT / "prompts" / "cleanup-reviewer.md"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verification = load_module("run_verification", SCRIPTS / "run-verification.py")
red_gate = load_module("record_red_gate", SCRIPTS / "record-red-gate.py")
snapshot = load_module("capture_layer_snapshot", SCRIPTS / "capture-layer-snapshot.py")
layer_diff = load_module("create_layer_diff", SCRIPTS / "create-layer-diff.py")
layer_context = load_module("build_layer_context", SCRIPTS / "build-layer-context.py")
layer_router = load_module("route_selected_layer", SCRIPTS / "route-selected-layer.py")
cleanup_check = load_module("check_cleanup_result", SCRIPTS / "check-cleanup-result.py")
test_author_recorder = load_module("record_test_author", SCRIPTS / "record-test-author.py")


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def todo_text() -> str:
    return """---
status: draft
owner: agent
red_gate_state: missing
---

# Layer L10

## Behavior Contract

Given a pending order

## Evidence

Pending.

## Implementation Boundary

| Area | Allowed? | Notes |
| --- | --- | --- |
| `src/` | yes | production |
| `legacy/` | forbidden | do not touch |

## Decision Log

| Decision | Comment | Command/evidence | Timestamp |
| --- | --- | --- | --- |
"""


def red_gate_todo_text() -> str:
    return todo_text().replace(
        "## Behavior Contract\n",
        "## Red-Test Gate\n\n"
        "| State | Evidence command | Observed result | Waiver/approval reason | Production implementation may proceed |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| `blocked` | `go test ./...` | Not run. | Awaiting approval. | No |\n\n"
        "## Behavior Contract\n",
    )


class VerificationEvidenceTests(unittest.TestCase):
    def test_retains_full_output_but_bounds_the_summary_by_bytes(self) -> None:
        result = verification.run(
            "python3 -c 'import sys; print(\"x\" * 12000); print(\"FAIL TestBounded\", file=sys.stderr); sys.exit(1)'",
            "unit",
            512,
        )
        evidence = Path(str(result["evidence_path"]))
        try:
            self.assertEqual(result["exit_code"], 1)
            self.assertTrue(result["summary_truncated"])
            self.assertLessEqual(result["excerpt_bytes"], 512)
            self.assertGreater(result["output_bytes"], 12_000)
            self.assertIn("FAIL TestBounded", evidence.read_text(encoding="utf-8"))
        finally:
            evidence.unlink(missing_ok=True)

    def test_tolerates_utf8_at_the_excerpt_boundary(self) -> None:
        excerpt, truncated = verification.bounded_excerpt("é" * 20, "", 9)
        self.assertTrue(truncated)
        self.assertLessEqual(len(excerpt.encode("utf-8")), 9)


class RedGateRecorderTests(unittest.TestCase):
    def test_records_evidence_without_granting_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = Path(directory) / "layer.todo.md"
            todo.write_text(red_gate_todo_text(), encoding="utf-8")
            result = red_gate.record_evidence(
                argparse.Namespace(
                    artifact=str(todo),
                    command="go test ./...",
                    exit_code=1,
                    evidence_path="/tmp/full.log",
                    output_bytes=9000,
                    excerpt="FAIL TestCancel",
                )
            )
            content = todo.read_text(encoding="utf-8")

        self.assertIn("status: needs-human-test-gate", content)
        self.assertIn("owner: human", content)
        self.assertIn("red_gate_state: blocked", content)
        self.assertIn("Exit 1 (non-zero); see Evidence.", content)
        self.assertIn("FAIL TestCancel", content)
        self.assertIn("9000", content)
        self.assertEqual(result["artifact_path"], str(todo))

    def test_creates_evidence_section_when_runtime_todo_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = Path(directory) / "layer.todo.md"
            content_without_evidence = todo_text().replace(
                "## Evidence\n\nPending.\n\n", ""
            )
            todo.write_text(content_without_evidence, encoding="utf-8")
            result = red_gate.record_evidence(
                argparse.Namespace(
                    artifact=str(todo),
                    command="make test-noisy",
                    exit_code=2,
                    evidence_path="/tmp/full.log",
                    output_bytes=50310,
                    excerpt="FAIL package [build failed]",
                )
            )
            content = todo.read_text(encoding="utf-8")

        self.assertEqual(content.count("## Evidence"), 1)
        self.assertIn("FAIL package [build failed]", content)
        self.assertIn("make test-noisy exited 2", result["summary"])
        self.assertIn("## Decision Log", content)

    def test_valid_nested_gate_decision_advances_and_preserves_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = Path(directory) / "layer.todo.md"
            original = red_gate_todo_text()
            todo.write_text(original, encoding="utf-8")
            result = red_gate.record_decision(
                argparse.Namespace(
                    artifact=str(todo),
                    selection="confirm_red",
                    feedback="expected failure",
                    command="go test ./...",
                    exit_code=1,
                    evidence_path="/tmp/full.log",
                )
            )
            content = todo.read_text(encoding="utf-8")

        self.assertTrue(result["proceed"])
        self.assertIn("status: ready-for-implementation", content)
        self.assertIn("red_gate_state: observed-red", content)
        self.assertIn("| `observed-red` | `go test ./...` | Exit 1; see Evidence. | expected failure | Yes |", content)
        self.assertIn("expected failure", content)
        self.assertIn("Given a pending order", content)

    def test_contradictory_decision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = Path(directory) / "layer.todo.md"
            todo.write_text(red_gate_todo_text(), encoding="utf-8")
            result = red_gate.record_decision(
                argparse.Namespace(
                    artifact=str(todo),
                    selection="confirm_red",
                    feedback="",
                    command="go test ./...",
                    exit_code=0,
                    evidence_path="/tmp/full.log",
                )
            )
            content = todo.read_text(encoding="utf-8")

        self.assertFalse(result["proceed"])
        self.assertIn("status: needs-human-test-gate", content)
        self.assertIn("| `blocked` | `go test ./...` | Exit 0; see Evidence. | blocked: selection contradicts exit code | No |", content)
        self.assertIn("blocked: selection contradicts exit code", content)


class LayerDiffTests(unittest.TestCase):
    def initialize_repository(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "src").mkdir()
        (root / "legacy").mkdir()
        (root / "src" / "order.txt").write_text("base\n", encoding="utf-8")
        (root / "legacy" / "old.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        todo = root / ".github" / "plans" / "slice" / "L10.todo.md"
        todo.parent.mkdir(parents=True)
        todo.write_text(todo_text(), encoding="utf-8")
        return todo

    def test_second_snapshot_excludes_the_first_layers_change_to_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            todo = self.initialize_repository(root)
            first_snapshot = str(snapshot.capture()["snapshot_tree"])
            (root / "src" / "order.txt").write_text("base\nlayer one\n", encoding="utf-8")
            first = layer_diff.create(first_snapshot, todo, 16_384)
            second_snapshot = str(snapshot.capture()["snapshot_tree"])
            (root / "src" / "order.txt").write_text("base\nlayer one\nlayer two\n", encoding="utf-8")
            second = layer_diff.create(second_snapshot, todo, 16_384)
            Path(str(first["patch_path"])).unlink(missing_ok=True)
            Path(str(second["patch_path"])).unlink(missing_ok=True)

        self.assertIn("+layer one", first["bounded_patch"])
        self.assertNotIn("+layer one", second["bounded_patch"])
        self.assertIn("+layer two", second["bounded_patch"])
        self.assertEqual(second["boundary_status"], "passed")

    def test_forbidden_path_is_a_deterministic_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            todo = self.initialize_repository(root)
            before = str(snapshot.capture()["snapshot_tree"])
            (root / "legacy" / "old.txt").write_text("changed\n", encoding="utf-8")
            result = layer_diff.create(before, todo, 512)
            Path(str(result["patch_path"])).unlink(missing_ok=True)

        self.assertEqual(result["boundary_status"], "violated")
        self.assertEqual(result["boundary_violations"], ["legacy/old.txt"])

    def test_active_todo_update_is_not_a_production_boundary_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            todo = self.initialize_repository(root)
            before = str(snapshot.capture()["snapshot_tree"])
            todo.write_text(todo_text() + "\nImplementation progress.\n", encoding="utf-8")
            result = layer_diff.create(before, todo, 512)
            Path(str(result["patch_path"])).unlink(missing_ok=True)

        self.assertEqual(result["boundary_status"], "passed")
        self.assertEqual(result["boundary_violations"], [])
        self.assertEqual(
            result["boundary_exempt_paths"],
            [".github/plans/slice/L10.todo.md"],
        )

    def test_multi_column_runtime_boundary_keeps_forbidden_paths_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = Path(directory) / "L10.todo.md"
            todo.write_text(
                """## Implementation Boundary

| Allowed Areas | Forbidden Areas | Top-Level Behavior Limits | Read-Only Tests |
| --- | --- | --- | --- |
| `internal/orders/order.go`; `internal/orders/*_test.go` | `.benchmark/`, `internal/legacy/`, `internal/httpapi/` | Add `StatusCancelled` and `Order.Cancel()` only. | `internal/orders/service_test.go` |

## Task Board
""",
                encoding="utf-8",
            )
            allowed, forbidden = layer_diff.boundary_paths(todo)

        self.assertEqual(
            allowed,
            ["internal/orders/*_test.go", "internal/orders/order.go"],
        )
        self.assertEqual(
            forbidden,
            [".benchmark", "internal/httpapi", "internal/legacy", "internal/orders/service_test.go"],
        )
        classification = layer_diff.classify(
            ["internal/orders/order.go", "internal/legacy/cancel.go"],
            allowed,
            forbidden,
        )
        self.assertEqual(classification["boundary_status"], "violated")
        self.assertEqual(
            classification["boundary_violations"], ["internal/legacy/cancel.go"]
        )
        self.assertEqual(
            layer_diff.classify(["internal/orders/service_test.go"], allowed, forbidden)["boundary_status"],
            "violated",
        )


class LayerContextTests(unittest.TestCase):
    def create_project(self, root: Path) -> tuple[Path, Path]:
        plan = root / ".github" / "plans" / "slice"
        todo = plan / "layers" / "L10-domain.md"
        todo.parent.mkdir(parents=True)
        layer_map = plan / "01-layer-map.md"
        layer_map.write_text(
            """---
selected_layer: L10-domain
---

# Layer map

L10 owns order cancellation.
""",
            encoding="utf-8",
        )
        todo.write_text(
            """---
selected_layer: L10-domain
---

# L10 Domain

## Behavior Contract

Given a pending order, when it is cancelled, then its state is cancelled.

## Implementation Boundary

| Allowed Areas | Forbidden Areas | Top-Level Behavior Limits | Read-Only Tests |
| --- | --- | --- | --- |
| `internal/orders/order.go`; `internal/orders/*_test.go` | `internal/legacy/`; `Makefile` | Add cancellation only. | `internal/orders/service_test.go` |

## Task Board
""",
            encoding="utf-8",
        )
        orders = root / "internal" / "orders"
        orders.mkdir(parents=True)
        (orders / "order.go").write_text("package orders\n\ntype Order struct{}\n", encoding="utf-8")
        (orders / "order_test.go").write_text("package orders\n", encoding="utf-8")
        (orders / "service_test.go").write_text("package orders\n// read only\n", encoding="utf-8")
        legacy = root / "internal" / "legacy"
        legacy.mkdir(parents=True)
        (legacy / "secret.go").write_text("package legacy\n// MUST NOT LEAK\n", encoding="utf-8")
        return layer_map, todo

    def test_projection_is_bounded_and_excludes_forbidden_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            layer_map, _ = self.create_project(root)
            request = "Add func (o Order) Cancel() (updated Order, changed bool, err error)."
            result = layer_context.build("todo-generator", layer_map, None, request)
            manifest = Path(str(result["manifest_path"]))
            manifest_content = manifest.read_text(encoding="utf-8")

        self.assertEqual(result["selected_layer"], "L10-domain")
        self.assertEqual(result["allowed_paths"], ["internal/orders/*_test.go", "internal/orders/order.go"])
        self.assertEqual(result["forbidden_paths"], ["Makefile", "internal/legacy"])
        self.assertEqual(result["read_only_paths"], ["internal/orders/service_test.go"])
        self.assertIn("internal/orders/order.go", result["production_files"])
        self.assertIn("internal/orders/order_test.go", result["test_files"])
        self.assertIn("internal/orders/service_test.go", result["test_files"])
        self.assertLessEqual(result["source_context_bytes"], layer_context.CONSUMER_BUDGETS["todo-generator"])
        self.assertNotIn("MUST NOT LEAK", result["source_context"])
        self.assertEqual(result["request_contract"], request)
        self.assertIn('"sha256"', manifest_content)

    def test_projection_refreshes_after_an_allowed_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            _, todo = self.create_project(root)
            first = layer_context.build("implementor", None, todo)
            (root / "internal" / "orders" / "order.go").write_text(
                "package orders\n\ntype Order struct{ Cancelled bool }\n",
                encoding="utf-8",
            )
            second = layer_context.build("implementor", None, todo)

        self.assertNotEqual(first["source_fingerprint"], second["source_fingerprint"])
        first_hash = next(item["sha256"] for item in first["source_files"] if item["path"] == "internal/orders/order.go")
        second_hash = next(item["sha256"] for item in second["source_files"] if item["path"] == "internal/orders/order.go")
        self.assertNotEqual(first_hash, second_hash)

    def test_layer_size_is_approved_input_and_invalid_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            _, todo = self.create_project(Path(directory))
            self.assertEqual(layer_context.build("implementor", None, todo)["layer_size"], "standard")
            original = todo.read_text(encoding="utf-8")
            todo.write_text(original.replace("selected_layer: L10-domain", "selected_layer: L10-domain\nlayer_size: small"), encoding="utf-8")
            self.assertEqual(layer_context.build("test-author", None, todo)["layer_size"], "small")
            todo.write_text(original.replace("selected_layer: L10-domain", "selected_layer: L10-domain\nlayer_size: tiny"), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "invalid layer_size"):
                layer_context.build("implementor", None, todo)

    def test_test_author_and_implementor_receive_separate_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            _, todo = self.create_project(Path(directory))
            content = todo.read_text(encoding="utf-8")
            content = content.replace(
                "## Implementation Boundary\n",
                """## Test-Author Boundary

| Allowed Tests | Forbidden Areas | Read-Only Production |
| --- | --- | --- |
| `internal/orders/*_test.go` | `internal/legacy/` | `internal/orders/order.go` |

## Implementation Boundary
""",
            ).replace(
                "| `internal/orders/order.go`; `internal/orders/*_test.go` | `internal/legacy/`; `Makefile` | Add cancellation only. | `internal/orders/service_test.go` |",
                "| `internal/orders/order.go` | `internal/legacy/`; `Makefile` | Add cancellation only. | `internal/orders/*_test.go` |",
            )
            todo.write_text(content, encoding="utf-8")
            author = layer_context.build("test-author", None, todo)
            implementor = layer_context.build("implementor", None, todo)

        self.assertEqual(author["allowed_paths"], ["internal/orders/*_test.go"])
        self.assertEqual(author["read_only_paths"], ["internal/orders/order.go"])
        self.assertEqual(implementor["allowed_paths"], ["internal/orders/order.go"])
        self.assertEqual(implementor["read_only_paths"], ["internal/orders/*_test.go"])

    def test_legacy_combined_boundary_is_split_by_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            _, todo = self.create_project(Path(directory))
            author = layer_context.build("test-author", None, todo)
            implementor = layer_context.build("implementor", None, todo)

        self.assertEqual(author["allowed_paths"], ["internal/orders/*_test.go"])
        self.assertIn("internal/orders/order.go", author["read_only_paths"])
        self.assertEqual(implementor["allowed_paths"], ["internal/orders/order.go"])
        self.assertIn("internal/orders/*_test.go", implementor["read_only_paths"])

    def test_mapper_skeleton_permissions_are_tolerant_before_todo_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            layer_map, todo = self.create_project(root)
            todo.write_text(
                todo.read_text(encoding="utf-8").replace(
                    "| `internal/orders/order.go`; `internal/orders/*_test.go` | "
                    "`internal/legacy/`; `Makefile` | Add cancellation only. | "
                    "`internal/orders/service_test.go` |",
                    "| Change `internal/orders/order.go` and add focused tests under "
                    "`internal/orders/*_test.go`. | Do not touch `internal/legacy/` "
                    "or `Makefile`. | Preserve public behavior. | Keep "
                    "`internal/orders/service_test.go` unchanged. |",
                ),
                encoding="utf-8",
            )

            result = layer_context.build("todo-generator", layer_map, None)

        self.assertEqual(
            result["allowed_paths"],
            ["internal/orders/*_test.go", "internal/orders/order.go"],
        )
        self.assertEqual(result["forbidden_paths"], ["Makefile", "internal/legacy"])
        self.assertEqual(result["read_only_paths"], ["internal/orders/service_test.go"])

    def test_generated_todo_permissions_reject_mapper_style_prose(self) -> None:
        content = """## Implementation Boundary

| Allowed Areas | Forbidden Areas | Top-Level Behavior Limits | Read-Only Tests |
| --- | --- | --- | --- |
| Change `internal/orders/order.go`. | `internal/legacy/` | Preserve public behavior. | None |
"""

        with self.assertRaisesRegex(
            SystemExit,
            "allowed boundary cell must be exact None or only backticked",
        ):
            layer_context.boundary_paths(content, strict_permissions=True)

    def test_projection_rejects_exact_editable_read_only_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            _, todo = self.create_project(root)
            todo.write_text(
                todo.read_text(encoding="utf-8").replace(
                    "`internal/orders/order.go`; `internal/orders/*_test.go`",
                    "`internal/orders/order.go`; `internal/orders/*_test.go`; "
                    "`internal/orders/service_test.go`",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                SystemExit,
                "both allowed and read-only: internal/orders/service_test.go",
            ):
                layer_context.build("test-author", None, todo)

    def test_none_read_only_cell_does_not_capture_writable_path_from_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            _, todo = self.create_project(root)
            content = todo.read_text(encoding="utf-8")
            content = content.replace(
                "`internal/orders/order.go`; `internal/orders/*_test.go`",
                "`internal/orders/order.go`; `internal/orders/*_test.go`; "
                "`internal/orders/service_test.go`",
            ).replace(
                "`internal/orders/service_test.go` |\n\n## Task Board",
                "None |\n\n## Task Board\n\n"
                "Preserve existing behavior while extending "
                "`internal/orders/service_test.go`.\n",
            )
            todo.write_text(content, encoding="utf-8")

            result = layer_context.build("test-author", None, todo)

        self.assertIn("internal/orders/service_test.go", result["allowed_paths"])
        self.assertEqual(result["read_only_paths"], ["internal/orders/order.go"])

    def test_permission_cell_rejects_prose_that_mentions_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            _, todo = self.create_project(root)
            todo.write_text(
                todo.read_text(encoding="utf-8").replace(
                    "`internal/orders/service_test.go`",
                    "None mandated. Prefer coverage in `internal/orders/service_test.go`",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                SystemExit,
                "read-only boundary cell must be exact None or only backticked",
            ):
                layer_context.build("test-author", None, todo)

    def test_test_author_contract_projection_omits_review_and_risk_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            _, todo = self.create_project(root)
            todo.write_text(
                todo.read_text(encoding="utf-8")
                + "\n## Risk Board\n\nDO NOT REPLAY RISK HISTORY\n"
                + "\n## Decision Log\n\nDO NOT REPLAY DECISION HISTORY\n",
                encoding="utf-8",
            )
            result = layer_context.build("test-author", None, todo, "Exact API contract")

        self.assertIn("## Behavior Contract", result["layer_contract"])
        self.assertIn("## Implementation Boundary", result["layer_contract"])
        self.assertIn("## Task Board", result["layer_contract"])
        self.assertNotIn("DO NOT REPLAY RISK HISTORY", result["layer_contract"])
        self.assertNotIn("DO NOT REPLAY DECISION HISTORY", result["layer_contract"])
        self.assertLessEqual(result["source_context_bytes"], 10_000)


class TestAuthorRecorderTests(unittest.TestCase):
    def todo(self, root: Path) -> Path:
        todo = root / ".github" / "plans" / "slice" / "layers" / "L10.todo.md"
        todo.parent.mkdir(parents=True)
        todo.write_text(
            """---
status: ready-for-implementation
owner: human
workflow: layered-tdd
selected_layer: L10
test_ownership: human-written
red_gate_state: not-run-human-approved
---

## Task Board

| Task | Type | Owner | Status | Notes |
| --- | --- | --- | --- | --- |
| - [ ] Add contract test. | top-level-test | human | pending | approved Gherkin |
| - [ ] Implement behavior. | production | implementor | pending | later |

## Decision Log

| Decision | Comment | Command/evidence | Timestamp |
| --- | --- | --- | --- |
""",
            encoding="utf-8",
        )
        return todo

    def initialize_repository(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        source = root / "internal" / "orders"
        source.mkdir(parents=True)
        (source / "order.go").write_text("package orders\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)

    def test_prepare_records_gate_and_only_updates_top_level_test_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            todo = self.todo(Path(directory))
            result = test_author_recorder.prepare(todo, "cover the exact signature")
            content = todo.read_text(encoding="utf-8")

        self.assertTrue(result["proceed"])
        self.assertIn("test_ownership: agent-written-after-approval", content)
        self.assertIn("red_gate_state: blocked", content)
        self.assertIn("| - [ ] Add contract test. | top-level-test | agent | in-progress |", content)
        self.assertIn("| - [ ] Implement behavior. | production | implementor | pending |", content)
        self.assertIn("cover the exact signature", content)

    def test_complete_accepts_only_reported_in_boundary_test_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            self.initialize_repository(root)
            todo = self.todo(root)
            test_author_recorder.prepare(todo, "")
            manifest = todo.parent.parent / ".context" / "L10-test-author.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "allowed_paths": ["internal/orders/*_test.go"],
                "forbidden_paths": ["internal/legacy/**"],
                "read_only_paths": ["internal/orders/service_test.go"],
            }), encoding="utf-8")
            before = str(snapshot.capture()["snapshot_tree"])
            test_file = root / "internal" / "orders" / "order_test.go"
            test_file.write_text("package orders\n", encoding="utf-8")
            result = test_author_recorder.complete(
                todo, manifest, before, json.dumps(["internal/orders/order_test.go"])
            )
            content = todo.read_text(encoding="utf-8")

        self.assertTrue(result["proceed"])
        self.assertEqual(result["changed_files"], ["internal/orders/order_test.go"])
        self.assertIn("| - [x] Add contract test. | top-level-test | agent | done |", content)

    def test_complete_rejects_production_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            self.initialize_repository(root)
            todo = self.todo(root)
            test_author_recorder.prepare(todo, "")
            manifest = todo.parent.parent / ".context" / "L10-test-author.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "allowed_paths": ["internal/orders/order.go", "internal/orders/*_test.go"],
                "forbidden_paths": [], "read_only_paths": [],
            }), encoding="utf-8")
            before = str(snapshot.capture()["snapshot_tree"])
            (root / "internal" / "orders" / "order.go").write_text("package orders\nvar changed = true\n", encoding="utf-8")
            result = test_author_recorder.complete(
                todo, manifest, before, json.dumps(["internal/orders/order.go"])
            )

        self.assertFalse(result["proceed"])
        self.assertEqual(result["violations"], ["internal/orders/order.go"])

    def test_checkpoint_is_safe_only_when_author_changed_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory, working_directory(Path(directory)):
            root = Path(directory)
            self.initialize_repository(root)
            todo = self.todo(root)
            test_author_recorder.prepare(todo, "")
            manifest = todo.parent.parent / ".context" / "L10-test-author.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "allowed_paths": ["internal/orders/*_test.go"],
                "forbidden_paths": [], "read_only_paths": [],
            }), encoding="utf-8")
            before = str(snapshot.capture()["snapshot_tree"])
            result = test_author_recorder.complete(
                todo, manifest, before, "[]", checkpoint_required=True
            )

        self.assertTrue(result["checkpoint_required"])
        self.assertFalse(result["proceed"])
        self.assertEqual(result["changed_files"], [])


class LayerWorkKindRoutingTests(unittest.TestCase):
    def create_layer(self, root: Path, work_kind: str | None) -> Path:
        plan = root / ".github" / "plans" / "slice"
        layers = plan / "layers"
        layers.mkdir(parents=True)
        layer_map = plan / "01-layer-map.md"
        layer_map.write_text("---\nselected_layer: arbitrary-id\n---\n", encoding="utf-8")
        declaration = f"work_kind: {work_kind}\n" if work_kind is not None else ""
        (layers / "arbitrary-id.todo.md").write_text(
            f"---\nselected_layer: arbitrary-id\n{declaration}---\n",
            encoding="utf-8",
        )
        return layer_map

    def test_missing_work_kind_preserves_behavior_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = layer_router.classify(self.create_layer(Path(directory), None))
        self.assertEqual(result["work_kind"], "behavior")

    def test_numbered_todo_resolves_by_selected_layer_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layer_map = self.create_layer(Path(directory), "behavior")
            todo = layer_map.parent / "layers" / "arbitrary-id.todo.md"
            numbered = todo.with_name("10-arbitrary-id.todo.md")
            todo.rename(numbered)
            self.assertEqual(layer_router.classify(layer_map)["todo_path"], str(numbered))
            self.assertEqual(layer_context.selected_todo(layer_map, "arbitrary-id"), numbered)

    def test_cleanup_is_explicit_and_independent_of_layer_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = layer_router.classify(self.create_layer(Path(directory), "cleanup"))
        self.assertEqual(result["work_kind"], "cleanup")
        self.assertEqual(result["selected_layer"], "arbitrary-id")

    def test_unknown_work_kind_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layer_map = self.create_layer(Path(directory), "migration")
            with self.assertRaises(SystemExit):
                layer_router.classify(layer_map)


class CleanupResultTests(unittest.TestCase):
    def cleanup_todo(self, root: Path) -> Path:
        todo = root / ".github" / "plans" / "slice" / "layers" / "remove-old.todo.md"
        todo.parent.mkdir(parents=True)
        todo.write_text(
            """---
work_kind: cleanup
selected_layer: remove-old
---

## Removal Inventory

| Target | Kind | Replacement | Expected state |
| --- | --- | --- | --- |
| `legacy/old.go` | file | `src/new.go` | removed |

## Cleanup Contract

Remove one superseded private adapter without changing behavior.

## Supersession Chain

`src/new.go` replaced `legacy/old.go` and all consumers have migrated.

## Preservation Contract

Existing behavior tests remain unchanged.

## Remaining Reference Checks

| Pattern | Root | Reason |
| --- | --- | --- |
| `OldAdapter` | `src/` | stale registration |

## Implementation Boundary

Only `legacy/old.go` may be removed.

## Task Board

Remove the declared target.

## Risk Board

Dynamic references are checked by the bounded reference scan.

## Decision Log

Human approval pending.
""",
            encoding="utf-8",
        )
        return todo

    def test_passes_when_targets_and_references_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "new.go").write_text("package src\n", encoding="utf-8")
            result = cleanup_check.check(self.cleanup_todo(root), root)
        self.assertTrue(result["passed"])

    def test_fails_when_declared_target_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "legacy").mkdir()
            (root / "legacy" / "old.go").write_text("package legacy\n", encoding="utf-8")
            (root / "src").mkdir()
            result = cleanup_check.check(self.cleanup_todo(root), root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["remaining_targets"], ["legacy/old.go"])

    def test_fails_when_bounded_reference_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "registry.go").write_text("var x = OldAdapter{}\n", encoding="utf-8")
            result = cleanup_check.check(self.cleanup_todo(root), root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["remaining_references"], ["src/registry.go:1:OldAdapter"])

    def test_plan_validation_accepts_bounded_repository_relative_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            result = cleanup_check.validate_plan(self.cleanup_todo(root), root)
        self.assertTrue(result["passed"])


class WorkflowWiringTests(unittest.TestCase):
    def test_execution_model_follows_approved_layer_size(self) -> None:
        try:
            from jinja2 import Environment
        except ImportError:
            self.skipTest("Jinja2 is unavailable")

        workflow = WORKFLOW.read_text(encoding="utf-8")
        for agent, context in (
            ("agent_test_author", "test_author_context"),
            ("implementor", "implementation_context"),
            ("cleanup_implementor", "cleanup_implementation_context"),
        ):
            block = workflow.split(f"  - name: {agent}\n", 1)[1].split("\n  - name:", 1)[0]
            model = block.split('model: "', 1)[1].split('"', 1)[0]
            effort = block.split('effort: "', 1)[1].split('"', 1)[0]
            for size, expected_model, expected_effort in (
                ("small", "gpt-6-luna", "high"),
                ("standard", "claude-sonnet-5.5", "medium"),
            ):
                values = {context: {"output": {"layer_size": size}}}
                self.assertEqual(Environment().from_string(model).render(**values), expected_model)
                self.assertEqual(Environment().from_string(effort).render(**values), expected_effort)

    def test_first_layer_todo_revision_renders_without_later_gates(self) -> None:
        try:
            from jinja2 import Environment, StrictUndefined
        except ImportError:
            self.skipTest("Jinja2 is unavailable")
        prompt = (WORKFLOW_ROOT / "prompts" / "layer-todo-reviser.md").read_text(encoding="utf-8")
        rendered = Environment(undefined=StrictUndefined).from_string(prompt).render(
            layer_todo_generator={"output": {"artifact_path": "layers/L20.todo.md"}},
            layer_todo_gate={"output": {"additional_input": {"feedback": "Remove docs-only Gherkin"}}},
        )
        self.assertIn("layer todo gate: Remove docs-only Gherkin", rendered)
        self.assertIn("layer todo revision gate: ", rendered)

    def test_reviewer_has_no_raw_verification_stream_references(self) -> None:
        prompt = REVIEWER.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for stream in ("stdout", "stderr"):
            self.assertNotIn(f"verification_tests.output.{stream}", prompt)
            self.assertNotIn(f"verification_lint.output.{stream}", prompt)
            self.assertNotIn(f"verification_security.output.{stream}", prompt)
            self.assertNotIn(f"verification_tests.output.{stream}", workflow)
            self.assertNotIn(f"verification_lint.output.{stream}", workflow)
            self.assertNotIn(f"verification_security.output.{stream}", workflow)

    def test_gate_feedback_uses_runtime_shaped_additional_input(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("red_suite_evidence_gate.output.additional_input.feedback?", workflow)
        self.assertNotIn("red_suite_evidence_gate.output.feedback?", workflow)

    def test_mechanical_recorders_are_scripts_not_models(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for name in (
            "red_suite_evidence_recorder", "red_gate_decision_recorder",
            "test_author_preparer", "test_author_completion_recorder",
        ):
            block = workflow.split(f"  - name: {name}\n", 1)[1].split("\n  - name:", 1)[0]
            self.assertIn("type: script", block)
            self.assertNotIn("model:", block)
            self.assertNotIn("prompt:", block)

    def test_expensive_layer_agents_receive_bounded_context_projections(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        expected = {
            "selected_layer_context": "todo-generator",
            "test_author_context": "test-author",
            "implementation_context": "implementor",
        }
        for name, consumer in expected.items():
            block = workflow.split(f"  - name: {name}\n", 1)[1].split("\n  - name:", 1)[0]
            self.assertIn("type: script", block)
            self.assertIn("build-layer-context.py", block)
            self.assertIn(f"- {consumer}", block)

        self.assertIn("route: test_author_preparer", workflow)
        self.assertIn("to: test_author_context", workflow)
        self.assertNotIn("route: agent_test_author", workflow)
        self.assertIn("route: implementation_context", workflow)
        self.assertNotIn("route: implementor\n", workflow)

    def test_projection_prompts_forbid_broad_discovery_and_full_suite_replays(self) -> None:
        todo_prompt = TODO_GENERATOR.read_text(encoding="utf-8")
        test_prompt = TEST_AUTHOR.read_text(encoding="utf-8")
        implementor_prompt = IMPLEMENTOR.read_text(encoding="utf-8")

        for prompt in (todo_prompt, test_prompt, implementor_prompt):
            self.assertIn("source_context", prompt)
            self.assertIn("request_contract", prompt)
            self.assertIn("Do not scan or search the repository broadly", prompt)
        self.assertIn("Do not run tests, lint, or security commands", todo_prompt)
        self.assertIn("Do not run a targeted test command", test_prompt)
        self.assertIn("Do not rerun the full suite", implementor_prompt)

    def test_test_author_model_only_owns_test_code(self) -> None:
        prompt = TEST_AUTHOR.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        author = workflow.split("  - name: agent_test_author\n", 1)[1].split("\n  - name:", 1)[0]
        self.assertIn("Do not edit the todo", prompt)
        self.assertIn("do not reopen the manifest, todo, layer map", prompt)
        self.assertIn("to: test_author_completion_recorder", author)
        self.assertNotIn("to: red_suite_verifier", author)
        self.assertIn("test_author_completion_recorder.output", workflow)

    def test_cleanup_route_skips_behavior_test_author_and_red_gate(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        router = workflow.split("  - name: selected_layer_router\n", 1)[1].split("\n  - name:", 1)[0]
        self.assertIn("output.work_kind == 'cleanup'", router)
        self.assertIn("to: cleanup_plan_context", router)
        cleanup_section = workflow.split("  - name: cleanup_plan_context\n", 1)[1].split(
            "\n  - name: selected_layer_context", 1
        )[0]
        self.assertNotIn("to: agent_test_author", cleanup_section)
        self.assertNotIn("to: red_suite_verifier", cleanup_section)
        self.assertNotIn("to: implementor\n", cleanup_section)
        self.assertIn("to: cleanup_implementor", cleanup_section)

    def test_cleanup_prompts_reject_implementation_coupled_absence_tests(self) -> None:
        for prompt_path in (CLEANUP_PLANNER, CLEANUP_IMPLEMENTOR, CLEANUP_REVIEWER):
            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn("not called", prompt)
        planner = CLEANUP_PLANNER.read_text(encoding="utf-8")
        self.assertIn("behavior/test-hardening", planner)
        self.assertIn("public API", planner)

    def test_cleanup_agents_receive_bounded_context(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for name, consumer in {
            "cleanup_plan_context": "cleanup-planner",
            "cleanup_implementation_context": "cleanup-implementor",
        }.items():
            block = workflow.split(f"  - name: {name}\n", 1)[1].split("\n  - name:", 1)[0]
            self.assertIn("build-layer-context.py", block)
            self.assertIn(f"- {consumer}", block)


if __name__ == "__main__":
    unittest.main()
