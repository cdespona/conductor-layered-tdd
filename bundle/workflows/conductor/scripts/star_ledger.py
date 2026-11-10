"""Hash-chained ledger for the star workflow.

The ledger lives at ``.github/plans/<slug>/star-state.json`` and holds two
parts: an append-only list of events chained by SHA-256 and a snapshot with
the phase cursor and the facts. Only ``star-step.py`` (the guard) writes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = 1
LEDGER_NAME = "star-state.json"
PENDING_DIR = Path(".github/plans/.star-pending")
GENESIS = "0" * 64


class LedgerError(Exception):
    """The ledger is missing, malformed or its hash chain is broken."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def empty_snapshot() -> dict[str, Any]:
    return {
        "status": "running",
        "objective": "",
        "plan_dir": "",
        "cursor": {"spoke": "", "phase": 0, "sub": 0, "layer": None},
        "facts": {},
        "layers": {"current": None, "selected": [], "closed": []},
        "escalations": {"used": 0, "max": 5},
        "pending": None,
        "hub_request": None,
        "hub_vetoes": 0,
        "visits": [],
        "incoherences": [],
        "last": {},
        "entered": None,
        "objective_attempts": 0,
        "violation": None,
    }


class Ledger:
    def __init__(self, path: Path, events: list[dict[str, Any]] | None = None,
                 snapshot: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.events = events or []
        self.snapshot = snapshot or empty_snapshot()

    # -- persistence -----------------------------------------------------
    @property
    def head(self) -> str:
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, event_type: str, /, **data: Any) -> dict[str, Any]:
        event = {"seq": len(self.events) + 1, "ts": now(), "type": event_type, "data": data,
                 "prev": self.head}
        event["hash"] = sha256(event["prev"] + canonical({k: event[k] for k in ("seq", "ts", "type", "data")}))
        self.events.append(event)
        return event

    def document(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "head": self.head,
            "snapshot_digest": sha256(self.head + canonical(self.snapshot)),
            "snapshot": self.snapshot,
            "events": self.events,
        }

    def save(self) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.document(), indent=2, ensure_ascii=False) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=".star-state-", dir=self.path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(temporary, self.path)
        return file_sha(self.path)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LedgerError(f"cannot read ledger {path}: {error}") from error
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise LedgerError(f"unsupported ledger schema in {path}")
        ledger = cls(path, document.get("events") or [], document.get("snapshot") or {})
        ledger.verify(document)
        return ledger

    def verify(self, document: dict[str, Any] | None = None) -> None:
        previous = GENESIS
        for index, event in enumerate(self.events, 1):
            body = {k: event.get(k) for k in ("seq", "ts", "type", "data")}
            if event.get("seq") != index or event.get("prev") != previous:
                raise LedgerError(f"ledger chain broken at event {index}")
            if event.get("hash") != sha256(previous + canonical(body)):
                raise LedgerError(f"ledger hash mismatch at event {index}")
            previous = event["hash"]
        if document is not None:
            if document.get("head") != previous:
                raise LedgerError("ledger head does not match the last event")
            if document.get("snapshot_digest") != sha256(previous + canonical(self.snapshot)):
                raise LedgerError("ledger snapshot was modified outside the guard")

    def relocate(self, target: Path) -> None:
        target = Path(target)
        if target.resolve() == self.path.resolve():
            return
        old = self.path
        self.append("ledger_relocated", source=str(old), target=str(target))
        self.path = target
        self.save()
        if old.is_file():
            old.unlink()


def ledger_path(plan_dir: str | Path) -> Path:
    return Path(plan_dir) / LEDGER_NAME


def archive(path: Path) -> Path | None:
    """Move an existing ledger aside so a new run starts with a fresh chain."""
    path = Path(path)
    if not path.is_file():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"star-state.{stamp}.json")
    os.replace(path, target)
    return target
