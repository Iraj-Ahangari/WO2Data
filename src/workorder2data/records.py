"""Output records of the converter and their writers (JSONL master, flat CSV for filling in unknowns)."""

import csv
from pathlib import Path

from pydantic import BaseModel, Field

from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Prediction, write_jsonl

# warning reasons
NOT_STATED = "not_stated"
NOT_IN_TAXONOMY = "not_in_taxonomy"
EVIDENCE_NOT_FOUND = "evidence_not_found"
CLASS_UNKNOWN = "class_unknown"
NO_TAXONOMY_DATA = "no_taxonomy_data"
CLASS_CONFLICT_TAG = "class_conflicts_with_tag"
CONFLICTING = "conflicting"
UNVERIFIED_TAXONOMY = "unverified_taxonomy"
MODEL_ERROR = "model_error"

REVIEW_REASONS = {NOT_IN_TAXONOMY, EVIDENCE_NOT_FOUND, CLASS_CONFLICT_TAG, CONFLICTING, MODEL_ERROR, NO_TAXONOMY_DATA}


class FieldValue(BaseModel):
    value: str = UNKNOWN
    name: str | None = None
    evidence: str | None = None                 # verbatim span of the source text
    taxonomy_verified: bool | None = None       # True only if the matched taxonomy row is verified


class Warn(BaseModel):
    field: str | None = None
    reason: str
    detail: str | None = None


class Record(BaseModel):
    record_id: str
    source_id: str
    tag: str | None = None
    source_date: str | None = None
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    warnings: list[Warn] = Field(default_factory=list)
    needs_review: bool = False
    meta: dict = Field(default_factory=dict)

    def to_labeled(self) -> LabeledRecord:
        return LabeledRecord(
            labels={f: self.fields.get(f, FieldValue()).value for f in LABEL_FIELDS},
            phrases={f: v.evidence for f, v in self.fields.items() if v.evidence},
        )


def to_predictions(records: list[Record], model: str | None = None) -> list[Prediction]:
    by_source: dict[str, list[LabeledRecord]] = {}
    for r in records:
        by_source.setdefault(r.source_id, []).append(r.to_labeled())
    return [Prediction(source_id=sid, records=recs, meta={"model": model} if model else {}) for sid, recs in by_source.items()]


def write_records_jsonl(path: Path, records: list[Record]) -> None:
    write_jsonl(path, records)


def write_records_csv(path: Path, records: list[Record]) -> None:
    """Flat view: per field a `<field>` code column (blank when unknown, for the user to fill) and `<field>_name`."""
    header = ["source_id", "record_id", "tag", "source_date"]
    for f in LABEL_FIELDS:
        header += [f, f"{f}_name"]
    header += ["needs_review", "warnings"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in records:
            row = [r.source_id, r.record_id, r.tag or "", r.source_date or ""]
            for f in LABEL_FIELDS:
                v = r.fields.get(f, FieldValue())
                row += ["" if v.value == UNKNOWN else v.value, v.name or ""]
            warn = "; ".join(f"{x.field or 'record'}:{x.reason}" for x in r.warnings)
            row += ["yes" if r.needs_review else "", warn]
            w.writerow(row)
