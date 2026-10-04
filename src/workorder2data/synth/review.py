"""Human review of gold labels: export a CSV, edit it (codes), import the corrections.

Cells read `<code> | <name>` (or `unknown`). To correct a label, replace the cell with the right code
(or `unknown`); everything after ` | ` is ignored. Only rows with `ok` = y are applied; a sample is
marked reviewed once every one of its record rows is ok.
"""

import csv
from pathlib import Path

from workorder2data.schema import LABEL_FIELDS, MAINTENANCE_CATEGORIES, UNKNOWN, Sample
from workorder2data.taxonomy import Taxonomy

OK_VALUES = {"y", "yes", "1", "ok"}
SEP = " | "


class ReviewError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def _name(tax: Taxonomy, field: str, value: str) -> str:
    if value == UNKNOWN:
        return ""
    table = {
        "equipment_class": tax.classes,
        "subunit": tax.subunits,
        "maintainable_item": tax.items,
        "failure_mechanism": tax.mechanisms,
        "failure_cause": tax.causes,
        "detection_method": tax.detection_methods,
        "maintenance_activity": tax.activities,
    }.get(field)
    if table is not None:
        return table[value]["name"]
    return value if field == "maintenance_category" else ""


def _cell(tax: Taxonomy, field: str, value: str, class_id: str, mode_names: dict[str, str]) -> str:
    if value == UNKNOWN:
        return UNKNOWN
    name = mode_names.get(value, "") if field == "failure_mode" else _name(tax, field, value)
    return f"{value}{SEP}{name}" if name and name != value else value


def export_csv(samples: list[Sample], tax: Taxonomy, path: Path, split: str | None = None) -> int:
    rows = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "record", "kind", "text", *LABEL_FIELDS, "ok", "notes"])
        for s in samples:
            if split and s.split != split:
                continue
            for ri, rec in enumerate(s.records):
                cid = rec.value("equipment_class")
                mode_names = {r["code"]: r["name"] for r in tax.failure_modes_by_class.get(cid, [])}
                w.writerow([s.source_id, ri, s.kind, s.text if ri == 0 else "(same text)",
                            *[_cell(tax, fld, rec.value(fld), cid, mode_names) for fld in LABEL_FIELDS], "", ""])
                rows += 1
    return rows


def _validate(tax: Taxonomy, field: str, value: str, labels: dict[str, str]) -> str | None:
    """Return an error message, or None if `value` is a valid taxonomy value for this field."""
    if value == UNKNOWN:
        return None
    cid = labels.get("equipment_class", UNKNOWN)
    if field == "equipment_class":
        return None if value in tax.classes else "not an equipment class id"
    if field == "subunit":
        if value not in tax.subunits:
            return "not a subunit id"
        return None if cid in (UNKNOWN, tax.subunits[value]["class_id"]) else "subunit belongs to another class"
    if field == "maintainable_item":
        if value not in tax.items:
            return "not a maintainable-item id"
        sub = labels.get("subunit", UNKNOWN)
        return None if sub in (UNKNOWN, tax.items[value]["subunit_id"]) else "item belongs to another subunit"
    if field == "failure_mode":
        codes = {r["code"] for r in tax.failure_modes_by_class.get(cid, [])} if cid != UNKNOWN else {
            r["code"] for rows in tax.failure_modes_by_class.values() for r in rows}
        return None if value in codes else "not a failure mode for this class"
    if field == "maintenance_category":
        return None if value in MAINTENANCE_CATEGORIES else "must be corrective or preventive"
    table = {"failure_mechanism": tax.mechanisms, "failure_cause": tax.causes,
             "detection_method": tax.detection_methods, "maintenance_activity": tax.activities}[field]
    return None if value in table else "not a valid code"


def import_csv(samples: list[Sample], tax: Taxonomy, path: Path) -> tuple[int, int]:
    """Apply `ok` rows. Returns (rows applied, samples now reviewed). Raises ReviewError listing every bad cell."""
    by_id = {s.source_id: s for s in samples}
    errors: list[str] = []
    updates: list[tuple[Sample, int, dict[str, str]]] = []
    ok_rows: dict[str, set[int]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            if (row.get("ok") or "").strip().lower() not in OK_VALUES:
                continue
            s = by_id.get(row["source_id"])
            if s is None or not row["record"].isdigit() or int(row["record"]) >= len(s.records):
                errors.append(f"line {lineno}: unknown source_id/record {row['source_id']}/{row['record']}")
                continue
            ri = int(row["record"])
            labels = {fld: (row[fld].split(SEP)[0].strip() or UNKNOWN) for fld in LABEL_FIELDS}
            for fld in LABEL_FIELDS:  # validate in dependency order against the corrected row itself
                err = _validate(tax, fld, labels[fld], labels)
                if err:
                    errors.append(f"line {lineno} {row['source_id']} {fld}={labels[fld]!r}: {err}")
            updates.append((s, ri, labels))
            ok_rows.setdefault(s.source_id, set()).add(ri)
    if errors:
        raise ReviewError(errors)
    for s, ri, labels in updates:
        rec = s.records[ri]
        for fld in LABEL_FIELDS:
            if labels[fld] != rec.labels.get(fld, UNKNOWN):
                rec.phrases.pop(fld, None)
        rec.labels = labels
    reviewed = 0
    for sid, done in ok_rows.items():
        s = by_id[sid]
        if done == set(range(len(s.records))):
            s.reviewed = True
            reviewed += 1
    return len(updates), reviewed
