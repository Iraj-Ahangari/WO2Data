"""Library entry point: raw work-order text -> ISO 14224 records. The CLIs are thin wrappers over this."""

import os
from collections import Counter
from pathlib import Path
from typing import Callable

from workorder2data.extractor import Extractor
from workorder2data.glossary import load_glossary
from workorder2data.inputs import WorkOrder
from workorder2data.llm import (
    EXTRACTOR_EFFORT_DEFAULT, EXTRACTOR_MODEL_DEFAULT, AnthropicClient, LLMClient, load_dotenv,
)
from workorder2data.records import NOT_STATED, UNVERIFIED_TAXONOMY, Record
from workorder2data.schema import LABEL_FIELDS, UNKNOWN
from workorder2data.tags import load_tag_map
from workorder2data.taxonomy import Taxonomy


def make_extractor(
    *,
    model: str | None = None,
    glossary_path: Path | None = None,
    tag_map_path: Path | None = None,
    taxonomy_dir: Path | None = None,
    client: LLMClient | None = None,
) -> Extractor:
    tax = Taxonomy(taxonomy_dir)
    if client is None:
        load_dotenv()
        client = AnthropicClient(effort=os.environ.get("W2D_EXTRACTOR_EFFORT", EXTRACTOR_EFFORT_DEFAULT))
    return Extractor(
        tax,
        client,
        model=model or os.environ.get("W2D_EXTRACTOR_MODEL", EXTRACTOR_MODEL_DEFAULT),
        glossary=load_glossary(glossary_path),
        tag_map=load_tag_map(tag_map_path, set(tax.classes)),
    )


def convert(
    text: str,
    *,
    extractor: Extractor | None = None,
    source_id: str = "wo",
    tag: str | None = None,
    source_date: str | None = None,
) -> list[Record]:
    """Convert one work order into one record per distinct failure/maintenance event."""
    extractor = extractor or make_extractor()
    return extractor.convert(text, source_id=source_id, tag=tag, source_date=source_date)


def convert_many(
    extractor: Extractor,
    workorders: list[WorkOrder],
    on_records: Callable[[WorkOrder, list[Record]], None] = lambda wo, recs: None,
) -> list[Record]:
    out: list[Record] = []
    for wo in workorders:
        recs = extractor.convert(wo.text, source_id=wo.source_id, tag=wo.tag, source_date=wo.source_date)
        out += recs
        on_records(wo, recs)
    return out


def summarize(records: list[Record], n_workorders: int) -> str:
    unknown = Counter(f for r in records for f in LABEL_FIELDS if r.fields[f].value == UNKNOWN)
    reasons = Counter(w.reason for r in records for w in r.warnings if w.reason not in (NOT_STATED, UNVERIFIED_TAXONOMY))
    review = sum(r.needs_review for r in records)
    unverified = sum(any(w.reason == UNVERIFIED_TAXONOMY for w in r.warnings) for r in records)
    lines = [
        f"{n_workorders} work order(s) -> {len(records)} record(s); {review} need review",
        "unknown fields (to fill in): " + (", ".join(f"{f} {n}" for f, n in unknown.most_common()) or "none"),
    ]
    if reasons:
        lines.append("warnings: " + ", ".join(f"{k} {n}" for k, n in reasons.most_common()))
    if unverified:
        lines.append(f"{unverified} record(s) use taxonomy rows not yet verified by a domain expert")
    return "\n".join(lines)
