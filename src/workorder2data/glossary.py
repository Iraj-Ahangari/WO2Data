"""Site abbreviation glossary. Used only as hints in the prompt; the work-order text is never rewritten,
so evidence quotes stay verbatim."""

import csv
import re
from pathlib import Path


def load_glossary(path: Path | None) -> dict[str, str]:
    """CSV with columns `abbreviation,meaning` -> {lowercased abbreviation: meaning}."""
    if path is None:
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            r["abbreviation"].strip().lower(): r["meaning"].strip()
            for r in csv.DictReader(f)
            if r.get("abbreviation", "").strip()
        }


def hints_for(text: str, glossary: dict[str, str]) -> list[tuple[str, str]]:
    """Glossary entries whose abbreviation occurs in the text as a whole word (case-insensitive)."""
    found = []
    for abbr, meaning in glossary.items():
        if re.search(rf"(?<![\w]){re.escape(abbr)}(?![\w])", text, flags=re.IGNORECASE):
            found.append((abbr, meaning))
    return found
