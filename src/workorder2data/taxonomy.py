"""Loads the ISO 14224 taxonomy tables from taxonomy/*.csv.

The CSVs are the single source of truth (transcribed from Resources/ISO-14224.pdf);
nothing in this package hardcodes taxonomy values.
"""

import csv
import os
from pathlib import Path

TAXONOMY_DIR = Path(
    os.environ.get("W2D_TAXONOMY_DIR", Path(__file__).resolve().parents[2] / "taxonomy")
)

TRUE_VALUES = {"true", "yes", "1"}


def load_table(name: str, taxonomy_dir: Path | None = None) -> list[dict]:
    """Read taxonomy/<name>.csv. `verified` becomes a bool, `source_page` an int."""
    path = (taxonomy_dir or TAXONOMY_DIR) / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. The ISO 14224 tables are not distributed with this repo; "
            "build them from your own licensed copy of the standard (see taxonomy/README.md) "
            "or point W2D_TAXONOMY_DIR at them."
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if "verified" in row:
            row["verified"] = row["verified"].strip().lower() in TRUE_VALUES
        if "source_page" in row:
            row["source_page"] = int(row["source_page"])
    return rows
