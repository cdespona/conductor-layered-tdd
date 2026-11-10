"""Spoke catalog, facts and the closed condition mini-language of the star router.

Conditions are JSON objects with exactly one operator:

- ``{"eq": ["fact.name", value]}``
- ``{"in": ["fact.name", [value, ...]]}``
- ``{"exists": "fact.name"}``
- ``{"not": condition}``
- ``{"all": [condition, ...]}`` / ``{"any": [condition, ...]}``

A stale fact never satisfies a condition (invariant I9): it reads as missing.
Fact names that start with ``layer.`` resolve against the current layer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


STAR_DIR = Path(__file__).resolve().parent.parent / "star"
OPERATORS = {"eq", "in", "exists", "not", "all", "any"}
MISSING = object()


class TableError(Exception):
    """The canonical table or the catalog is malformed."""


def canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(name: str, directory: Path | None = None) -> dict[str, Any]:
    path = (directory or STAR_DIR) / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TableError(f"cannot read {path}: {error}") from error


class Catalog:
    def __init__(self, document: dict[str, Any]) -> None:
        self.phases: list[str] = document["phases"]
        self.spokes: dict[str, dict[str, Any]] = document["spokes"]
        self.step_to_spoke: dict[str, str] = {}
        for name, spoke in self.spokes.items():
            if spoke["phase"] not in self.phases:
                raise TableError(f"spoke {name} uses unknown phase {spoke['phase']}")
            for step in spoke["steps"]:
                if step in self.step_to_spoke:
                    raise TableError(f"step {step} belongs to two spokes")
                self.step_to_spoke[step] = name

    @classmethod
    def load(cls, directory: Path | None = None) -> "Catalog":
        return cls(load_json("catalog.json", directory))

    def __contains__(self, spoke: str) -> bool:
        return spoke in self.spokes

    def phase_index(self, spoke: str) -> int:
        return self.phases.index(self.spokes[spoke]["phase"])

    def position(self, spoke: str) -> tuple[int, int]:
        return self.phase_index(spoke), int(self.spokes[spoke].get("sub", 0))

    def layer_scoped(self, spoke: str) -> bool:
        return self.spokes[spoke]["phase"] == "layer" and spoke != "layer-select"

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"spoke": name, "phase": spoke["phase"], "preconditions": spoke.get("preconditions", "")}
            for name, spoke in self.spokes.items()
        ]


class Facts:
    """Read-only view of the snapshot facts with layer resolution and staleness."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot

    @property
    def current_layer(self) -> str | None:
        return self.snapshot.get("layers", {}).get("current")

    def key(self, name: str, layer: str | None = None) -> str:
        if name.startswith("layer."):
            return f"L[{layer or self.current_layer}].{name[len('layer.'):]}"
        return name

    def record(self, name: str) -> dict[str, Any] | None:
        return self.snapshot.get("facts", {}).get(self.key(name))

    def get(self, name: str, default: Any = None) -> Any:
        record = self.record(name)
        if record is None or record.get("stale"):
            return default
        return record.get("value")

    def fresh(self, name: str) -> bool:
        record = self.record(name)
        return record is not None and not record.get("stale")

    def lookup(self, name: str) -> Any:
        record = self.record(name)
        if record is None or record.get("stale"):
            return MISSING
        return record.get("value")


def evaluate(condition: Any, facts: Facts) -> bool:
    if condition is None or condition == {}:
        return True
    if not isinstance(condition, dict) or len(condition) != 1:
        raise TableError(f"condition must have exactly one operator: {condition!r}")
    operator, argument = next(iter(condition.items()))
    if operator not in OPERATORS:
        raise TableError(f"unknown operator {operator!r}")
    if operator == "eq":
        name, expected = argument
        value = facts.lookup(name)
        return value is not MISSING and value == expected
    if operator == "in":
        name, options = argument
        value = facts.lookup(name)
        return value is not MISSING and value in options
    if operator == "exists":
        value = facts.lookup(argument)
        return value is not MISSING and value not in (None, "", [], {})
    if operator == "not":
        return not evaluate(argument, facts)
    if operator == "all":
        return all(evaluate(item, facts) for item in argument)
    return any(evaluate(item, facts) for item in argument)


def validate_condition(condition: Any) -> None:
    if condition is None or condition == {}:
        return
    if not isinstance(condition, dict) or len(condition) != 1:
        raise TableError(f"condition must have exactly one operator: {condition!r}")
    operator, argument = next(iter(condition.items()))
    if operator not in OPERATORS:
        raise TableError(f"unknown operator {operator!r}")
    if operator in {"eq", "in"}:
        if not (isinstance(argument, list) and len(argument) == 2 and isinstance(argument[0], str)):
            raise TableError(f"{operator} takes [fact, value]: {argument!r}")
        if operator == "in" and not isinstance(argument[1], list):
            raise TableError(f"in takes a list of values: {argument!r}")
    elif operator == "exists":
        if not isinstance(argument, str):
            raise TableError(f"exists takes a fact name: {argument!r}")
    elif operator == "not":
        validate_condition(argument)
    else:
        if not isinstance(argument, list) or not argument:
            raise TableError(f"{operator} takes a non-empty list: {argument!r}")
        for item in argument:
            validate_condition(item)


class Table:
    TERMINALS = {"$done"}

    def __init__(self, document: dict[str, Any], catalog: Catalog) -> None:
        self.rules: list[dict[str, Any]] = document["rules"]
        seen = set()
        for rule in self.rules:
            if rule["id"] in seen:
                raise TableError(f"duplicate rule id {rule['id']}")
            seen.add(rule["id"])
            if rule["after"] != "entry" and rule["after"] not in catalog:
                raise TableError(f"rule {rule['id']} follows unknown spoke {rule['after']}")
            if rule["next"] not in catalog and rule["next"] not in self.TERMINALS:
                raise TableError(f"rule {rule['id']} targets unknown spoke {rule['next']}")
            validate_condition(rule.get("when"))

    @classmethod
    def load(cls, catalog: Catalog, directory: Path | None = None) -> "Table":
        return cls(load_json("canonical-table.json", directory), catalog)

    def match(self, after: str, facts: Facts) -> list[dict[str, Any]]:
        return [rule for rule in self.rules if rule["after"] == after and evaluate(rule.get("when"), facts)]
