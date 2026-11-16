"""Layer todo names are owned by a script, never by the mapping agent."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_workflow_optimizations import SCRIPTS, WORKFLOW, WORKFLOW_ROOT, load_module

sys.path.insert(0, str(SCRIPTS))
import layer_matrix  # noqa: E402

router = load_module("route_selected_layer_names", SCRIPTS / "route-selected-layer.py")
STAR_WORKFLOW = WORKFLOW_ROOT / "layered-star.yaml"


def layer_map(rows: list[tuple[str, str]], extra: str = "") -> str:
    body = "\n".join(f"| {i} | `{layer}` | `behavior` | `{todo}` | Responsibility a\\|b |"
                     for i, (layer, todo) in enumerate(rows, 1))
    return ("---\nselected_layer: \"\"\n---\n# Layer map\n\n## Layer Matrix\n\n"
            "| Order | Layer ID | Work kind | Todo file | Responsibility |\n| --- | --- | --- | --- | --- |\n"
            f"{body}\n\n## Selection Board\n\n| Layer | Why |\n| --- | --- |\n| x | y |\n{extra}")


def todo(layer: str, status: str = "skeleton") -> str:
    return f"---\nstatus: {status}\nselected_layer: {layer}\nwork_kind: behavior\n---\n# {layer}\n"


class NameLayerTodosTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plan = Path(directory.name) / ".github/plans/slice"
        (self.plan / "layers").mkdir(parents=True)
        self.map = self.plan / "01-layer-map.md"

    def write(self, name: str, content: str) -> None:
        (self.plan / "layers" / name).write_text(content)

    def run_script(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "name-layer-todos.py"), str(self.map)],
                              capture_output=True, text=True)

    def names(self) -> list[str]:
        return sorted(p.name for p in (self.plan / "layers").iterdir())

    def test_agent_chosen_names_become_canonical_and_the_matrix_follows(self):
        self.map.write_text(layer_map([("L10-domain", "layers/L10-domain-todo.md"),
                                       ("L20-service", "layers/L20-service.todo.md")]))
        self.write("L10-domain-todo.md", todo("L10-domain"))
        self.write("L20-service.todo.md", todo("L20-service"))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), ["L10-domain.md", "L20-service.md"])
        self.assertEqual(json.loads(result.stdout)["renamed"],
                         {"L10-domain-todo.md": "L10-domain.md", "L20-service.todo.md": "L20-service.md"})
        text = self.map.read_text()
        self.assertIn("`layers/L10-domain.md`", text)
        self.assertNotIn("-todo.md", text)
        self.assertIn("Responsibility a\\|b", text)
        self.assertIn("## Selection Board", text)

    def test_canonical_names_are_left_alone(self):
        content = layer_map([("L10", "layers/L10.md")])
        self.map.write_text(content)
        self.write("L10.md", todo("L10"))
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.map.read_text(), content)

    def test_frontmatter_matches_a_todo_with_an_unrelated_name(self):
        self.map.write_text(layer_map([("L10", "layers/first.md")]))
        self.write("domain-step.md", todo("L10"))
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.names(), ["L10.md"])

    def test_missing_todo_fails_before_any_rename(self):
        self.map.write_text(layer_map([("L10", "layers/L10-todo.md"), ("L20", "layers/nowhere.md")]))
        self.write("L10-todo.md", todo("L10"))
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no todo file found for layer(s) L20", result.stderr)
        self.assertEqual(self.names(), ["L10-todo.md"])

    def test_superseded_todos_are_never_adopted(self):
        self.map.write_text(layer_map([("L10", "layers/L10-todo.md")]))
        self.write("L10-todo.md", todo("L10", status="superseded"))
        self.assertEqual(self.run_script().returncode, 1)

    def test_invalid_or_duplicate_ids_and_missing_matrix_fail(self):
        for content, message in (
            (layer_map([("L10", "layers/L10.md"), ("L10", "layers/L10.md")]), "duplicate layer id"),
            (layer_map([("L10 domain", "layers/x.md")]), "invalid layer id"),
            ("# Layer map\n\nNo table.\n", "no '## Layer Matrix' section"),
        ):
            with self.subTest(message=message):
                self.map.write_text(content)
                self.write("L10.md", todo("L10"))
                result = self.run_script()
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_router_prefers_canonical_then_the_matrix_column(self):
        self.map.write_text(layer_map([("L10", "layers/custom-name.md")]))
        self.write("custom-name.md", todo("L10"))
        self.assertEqual(router.selected_todo(self.map, "L10").name, "custom-name.md")
        self.write("L10.md", todo("L10"))
        self.assertEqual(router.selected_todo(self.map, "L10").name, "L10.md")


class NamingRoutesTests(unittest.TestCase):
    @staticmethod
    def blocks(path: Path) -> dict[str, str]:
        return {part.splitlines()[0]: part for part in path.read_text().split("  - name: ")[1:]}

    def test_both_flows_name_todos_after_mapping_and_revision(self):
        for path, after_map in ((WORKFLOW, "layer_selection_gate"), (STAR_WORKFLOW, "layer_map_gate")):
            with self.subTest(flow=path.name):
                blocks = self.blocks(path)
                self.assertIn("- to: layer_todo_names", blocks["layer_mapper"])
                self.assertIn("- to: layer_map_revision_todo_names", blocks["layer_map_reviser"])
                self.assertNotIn(f"- to: {after_map}\n", blocks["layer_mapper"])
                for step, gate in (("layer_todo_names", after_map),
                                   ("layer_map_revision_todo_names", "layer_map_revision_gate")):
                    self.assertIn("scripts/name-layer-todos.py", blocks[step])
                    self.assertIn(f"- to: {gate}\n        when: \"{{{{ output.exit_code == 0 }}}}\"", blocks[step])
                self.assertIn("route: layer_map_reviser", blocks["layer_todo_naming_gate"])
                self.assertIn("- layer_todo_naming_gate.output?", blocks["layer_map_reviser"])

    def test_linear_failure_goes_to_the_naming_gate_and_star_through_the_router(self):
        linear, star = self.blocks(WORKFLOW), self.blocks(STAR_WORKFLOW)
        for step in ("layer_todo_names", "layer_map_revision_todo_names"):
            self.assertTrue(linear[step].rstrip().endswith("- to: layer_todo_naming_gate"))
            self.assertTrue(star[step].rstrip().endswith("- to: star_step"))
        self.assertIn("when: \"{{ output.next == 'layer_todo_naming_gate' }}\"", star["star_step"])

    def test_mapper_prompt_names_the_canonical_path(self):
        prompt = (WORKFLOW_ROOT / "prompts" / "layer-mapper.md").read_text()
        self.assertIn("`layers/<layer id>.md`", prompt)
        reviser = (WORKFLOW_ROOT / "prompts" / "layer-map-reviser.md").read_text()
        self.assertIn("Todo naming failed: {{ step.output.stderr }}", reviser)


if __name__ == "__main__":
    unittest.main()
