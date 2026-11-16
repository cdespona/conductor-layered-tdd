"""Read the Layer Matrix of a layer map and name layer todos deterministically.

A layer todo always lives at ``layers/<layer id>.md`` next to the layer map.
Agents write the todo content; this module owns the file name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LAYER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HEADING = re.compile(r"(?m)^##\s+Layer Matrix\s*$")


@dataclass
class Row:
    line: int
    cells: list[str]
    layer_id: str
    todo: str


def todo_name(layer_id: str) -> str:
    return f"{layer_id}.md"


def todo_path(layer_id: str) -> str:
    return f"layers/{todo_name(layer_id)}"


def clean(cell: str) -> str:
    return cell.strip().strip("`").strip()


def split_row(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def key(header: str) -> str:
    return re.sub(r"[^a-z]", "", header.lower())


def matrix(text: str) -> tuple[list[str], int, list[Row]]:
    """Return the lines, the todo column index and the rows of the first matrix table."""
    lines = text.splitlines()
    match = HEADING.search(text)
    if not match:
        raise ValueError("layer map has no '## Layer Matrix' section")
    start = text[: match.end()].count("\n") + 1
    table: list[int] = []
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("|"):
            table.append(index)
        elif table or stripped.startswith("#"):
            break
    if len(table) < 2:
        raise ValueError("the Layer Matrix section has no table")
    headers = [key(cell) for cell in split_row(lines[table[0]])]
    if "layerid" not in headers or "todofile" not in headers:
        raise ValueError("the Layer Matrix table needs 'Layer ID' and 'Todo file' columns")
    id_column, todo_column = headers.index("layerid"), headers.index("todofile")
    rows = []
    for index in table[2:]:
        cells = split_row(lines[index])
        if len(cells) <= max(id_column, todo_column):
            raise ValueError(f"Layer Matrix row has too few cells: {lines[index].strip()}")
        rows.append(Row(index, cells, clean(cells[id_column]), clean(cells[todo_column])))
    if not rows:
        raise ValueError("the Layer Matrix table has no layers")
    return lines, todo_column, rows


def frontmatter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    values = {}
    for line in content[3:end if end >= 0 else 0].splitlines():
        name, sep, value = line.partition(":")
        if sep:
            values[name.strip()] = value.strip().strip("\"'")
    return values


def active(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and frontmatter(path).get("status") != "superseded"


def find_source(layers: Path, layer_id: str, todo_cell: str, claimed: set[str]) -> Path | None:
    """Find the todo an agent wrote for ``layer_id`` under another name."""
    def free(path: Path) -> bool:
        return path.parent == layers and path.name not in claimed and active(path)

    if todo_cell:
        named = (layers.parent / todo_cell)
        if named.resolve().parent == layers.resolve() and free(named):
            return named
    candidates = sorted(path for path in layers.glob("*.md") if free(path))
    declared = [path for path in candidates if frontmatter(path).get("selected_layer") == layer_id]
    if len(declared) == 1:
        return declared[0]
    suffixed = [path for path in candidates
                if path.name.lower() in {f"{layer_id}{sep}todo.md".lower() for sep in "-._"}]
    if len(suffixed) == 1:
        return suffixed[0]
    return None


def normalize(layer_map: Path) -> dict[str, object]:
    """Give every mapped layer its canonical todo file and rewrite the matrix to match."""
    text = layer_map.read_text(encoding="utf-8")
    lines, todo_column, rows = matrix(text)
    seen: set[str] = set()
    for row in rows:
        if not LAYER_ID.match(row.layer_id) or row.layer_id.endswith(".md"):
            raise ValueError(f"invalid layer id {row.layer_id!r}: use letters, digits, '.', '_' or '-'")
        if row.layer_id in seen:
            raise ValueError(f"duplicate layer id {row.layer_id!r} in the Layer Matrix")
        seen.add(row.layer_id)
    layers = layer_map.parent / "layers"
    if not layers.is_dir():
        raise ValueError(f"layer todo directory does not exist: {layers}")
    claimed = {todo_name(row.layer_id) for row in rows}
    moves: dict[str, Path] = {}
    missing = []
    for row in rows:
        target = layers / todo_name(row.layer_id)
        if active(target):
            continue
        if target.exists():
            raise ValueError(f"{target} exists but is not an active todo file")
        source = find_source(layers, row.layer_id, row.todo, claimed | {p.name for p in moves.values()})
        if source is None:
            missing.append(row.layer_id)
        else:
            moves[row.layer_id] = source
    if missing:
        raise ValueError(
            "no todo file found for layer(s) " + ", ".join(missing)
            + f"; write each layer's todo at {layers}/<layer id>.md"
        )
    renamed = {}
    for layer_id, source in moves.items():
        target = layers / todo_name(layer_id)
        source.rename(target)
        renamed[source.name] = target.name
    for row in rows:
        cells = list(row.cells)
        cells[todo_column] = f"`{todo_path(row.layer_id)}`"
        lines[row.line] = "| " + " | ".join(cells) + " |"
    updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if updated != text:
        layer_map.write_text(updated, encoding="utf-8")
    return {
        "artifact_path": str(layer_map),
        "layers": [row.layer_id for row in rows],
        "todo_paths": [str(layers / todo_name(row.layer_id)) for row in rows],
        "renamed": renamed,
        "summary": (f"Renamed {len(renamed)} layer todo(s) to layers/<layer id>.md."
                    if renamed else "Every layer todo already uses layers/<layer id>.md."),
    }


def todo_from_matrix(layer_map: Path, layer_id: str) -> Path | None:
    """Return the matrix's todo file for ``layer_id`` when it exists."""
    try:
        _, _, rows = matrix(layer_map.read_text(encoding="utf-8"))
    except ValueError:
        return None
    for row in rows:
        if row.layer_id == layer_id and row.todo:
            candidate = layer_map.parent / row.todo
            if candidate.is_file():
                return candidate
    return None
