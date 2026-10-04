"""Label fields and the on-disk record formats shared by the generator, eval and (later) extractor."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

UNKNOWN = "unknown"

# Taxonomy-coded fields compared in evaluation. Values are taxonomy ids/codes (see taxonomy.Taxonomy):
# equipment_class=class_id, subunit=subunit_id, maintainable_item=item_id, failure_mode=code,
# failure_mechanism/failure_cause/detection_method/maintenance_activity=code,
# maintenance_category=corrective|preventive.
LABEL_FIELDS = (
    "equipment_class",
    "subunit",
    "maintainable_item",
    "failure_mode",
    "failure_mechanism",
    "failure_cause",
    "detection_method",
    "maintenance_category",
    "maintenance_activity",
)
MAINTENANCE_CATEGORIES = ("corrective", "preventive")


class LabeledRecord(BaseModel):
    """One failure/maintenance event. Every field is a taxonomy value or `unknown`."""

    labels: dict[str, str]
    phrases: dict[str, str] = Field(default_factory=dict)  # field -> verbatim text span (synthetic review aid)

    def value(self, field: str) -> str:
        return self.labels.get(field, UNKNOWN)


class Sample(BaseModel):
    """A synthetic work order with its gold labels."""

    source_id: str
    split: Literal["dev", "test"]
    kind: Literal["single", "multi", "vague"]
    style: str
    text: str
    records: list[LabeledRecord]
    reviewed: bool = False
    synthetic: bool = True
    meta: dict = Field(default_factory=dict)


class Prediction(BaseModel):
    source_id: str
    records: list[LabeledRecord]
    meta: dict = Field(default_factory=dict)


def read_jsonl(path: Path, model: type[BaseModel]) -> list:
    with open(path, encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def write_jsonl(path: Path, items: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json() + "\n")


def extract_json_object(text: str) -> dict:
    """Parse the first balanced JSON object in model output (tolerates prose or code fences around it)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model output")
