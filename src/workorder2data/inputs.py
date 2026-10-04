"""Read work orders from CSV (configurable column mapping) or text files (one order, or many split on '---')."""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkOrder:
    source_id: str
    text: str                      # what the model sees; evidence must be quoted from this
    tag: str | None = None         # from a mapped column, when given
    source_date: str | None = None  # passed through unchanged
    extra: dict = field(default_factory=dict)  # other pass-through columns


@dataclass
class CsvMapping:
    id_col: str
    text_cols: list[str]
    tag_col: str | None = None
    date_col: str | None = None
    passthrough_cols: list[str] = field(default_factory=list)


def combine_text(parts: list[tuple[str, str]], tag: str | None = None) -> str:
    """Labelled concatenation of several text columns (empty ones skipped)."""
    lines = [f"Tag: {tag}"] if tag else []
    lines += [f"{label}: {value.strip()}" for label, value in parts if value and value.strip()]
    return "\n".join(lines)


def read_csv(path: Path, mapping: CsvMapping, delimiter: str = ",") -> list[WorkOrder]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        missing = [c for c in [mapping.id_col, *mapping.text_cols, mapping.tag_col, mapping.date_col] if c and c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: column(s) not found: {missing}; available: {reader.fieldnames}")
        for i, row in enumerate(reader, start=2):
            tag = (row.get(mapping.tag_col) or "").strip() or None if mapping.tag_col else None
            parts = [(c, row.get(c) or "") for c in mapping.text_cols]
            if not combine_text(parts):
                continue  # nothing written in the text columns
            text = combine_text(parts, tag)
            out.append(WorkOrder(
                source_id=(row[mapping.id_col] or "").strip() or f"{path.stem}#row{i}",
                text=text,
                tag=tag,
                source_date=(row.get(mapping.date_col) or "").strip() or None if mapping.date_col else None,
                extra={c: row.get(c, "") for c in mapping.passthrough_cols},
            ))
    return out


def split_text(raw: str) -> list[str]:
    """Split on lines consisting only of '---'; a file without such a line is one work order."""
    parts = re.split(r"(?m)^\s*---\s*$", raw)
    return [p.strip() for p in parts if p.strip()]


def read_text_file(path: Path) -> list[WorkOrder]:
    parts = split_text(path.read_text(encoding="utf-8"))
    if len(parts) == 1:
        return [WorkOrder(source_id=path.stem, text=parts[0])]
    return [WorkOrder(source_id=f"{path.stem}#{i}", text=p) for i, p in enumerate(parts, start=1)]
