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


class Taxonomy:
    """Indexed view over the taxonomy tables (all values come from the CSVs)."""

    def __init__(self, taxonomy_dir: Path | None = None):
        d = taxonomy_dir
        self.dir = d or TAXONOMY_DIR
        self.classes = {r["class_id"]: r for r in load_table("equipment_classes", d)}
        self.subunits_by_class: dict[str, list[dict]] = {}
        for r in load_table("subunits", d):
            self.subunits_by_class.setdefault(r["class_id"], []).append(r)
        self.subunits = {r["subunit_id"]: r for rows in self.subunits_by_class.values() for r in rows}
        self.items_by_subunit: dict[str, list[dict]] = {}
        for r in load_table("maintainable_items", d):
            r["item_id"] = f'{r["subunit_id"]}#{r["order"]}'
            self.items_by_subunit.setdefault(r["subunit_id"], []).append(r)
        self.items = {r["item_id"]: r for rows in self.items_by_subunit.values() for r in rows}
        self.mechanisms = {r["code"]: r for r in load_table("failure_mechanisms", d)}
        self.causes = {r["code"]: r for r in load_table("failure_causes", d)}
        self.detection_methods = {r["code"]: r for r in load_table("detection_methods", d)}
        self.activities = {r["code"]: r for r in load_table("maintenance_activities", d)}
        self.footnotes = load_table("footnotes", d)
        self._failure_mode_rows = load_table("failure_modes", d)
        aliases = {(r["table"], r["printed_code"]): r["class_id"] for r in load_table("failure_mode_class_aliases", d)}
        by_cat_code: dict[tuple[str, str], list[str]] = {}
        for cid, c in self.classes.items():
            by_cat_code.setdefault((c["category"], c["code"]), []).append(cid)
        self.failure_modes_by_class: dict[str, list[dict]] = {}
        for fm in self._failure_mode_rows:
            for code in fm["applies_to"].split(";"):
                if (fm["table"], code) in aliases:
                    targets = [aliases[(fm["table"], code)]]
                else:
                    targets = by_cat_code.get((fm["category"], code), [])
                for cid in targets:
                    self.failure_modes_by_class.setdefault(cid, []).append(fm)

    def class_ids_with_data(self) -> list[str]:
        """Classes that have both subunits and failure modes in the taxonomy."""
        return sorted(c for c in self.subunits_by_class if c in self.failure_modes_by_class)
