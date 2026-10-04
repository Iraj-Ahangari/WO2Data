"""Label-first synthetic work-order generation.

Flow per sample: sample a valid label set -> model checks it is physically plausible ->
decide which fields the text will reveal -> a model writes messy text revealing only those ->
validate -> store text with gold labels (hidden fields = `unknown`).
The writing model must differ from the extractor model, or the test measures nothing.
"""

import json
import random
from typing import Callable

from workorder2data.llm import LLMClient
from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Sample, extract_json_object
from workorder2data.synth.sampler import (
    Policy,
    allocate,
    describe,
    disclose,
    pick_kind,
    sample_full_labels,
    split_test_counts,
)
from workorder2data.taxonomy import Taxonomy

STYLES = {
    "terse_abbrev": "terse technician shorthand: lowercase, abbreviations (e.g. brg, mech seal, lkg, repl), little punctuation",
    "narrative": "a short plain-English narrative in full sentences, as a planner or supervisor might write",
    "typos": "informal writing with a few realistic typos and inconsistent capitalisation",
    "report_form": "a work report with labelled lines such as 'Problem:' and 'Action taken:' filled in tersely",
    "mixed_shorthand": "a mix of shorthand and complete phrases, with run-on notes as written at the end of a shift",
}

PLAUSIBILITY_SYSTEM = (
    "You are a senior maintenance and reliability engineer. Decide whether a described combination of equipment, "
    "failed part, failure mode, mechanism, cause, detection method and maintenance action could realistically occur "
    "on one work order. Be tolerant of unusual-but-possible cases; reject only physically implausible or "
    'self-contradictory ones. Reply with JSON only: {"plausible": true|false, "reason": "<one short sentence>"}'
)

WRITER_SYSTEM = (
    "You write realistic, messy free-text work-order entries as maintenance technicians and planners do in a CMMS. "
    "Reply with JSON only."
)

FACT_LABELS = {
    "equipment_class": "Equipment",
    "subunit": "Subsystem",
    "maintainable_item": "Part",
    "failure_mode": "What was observed (failure mode)",
    "failure_mechanism": "Failure mechanism",
    "failure_cause": "Underlying cause",
    "detection_method": "How it was found",
    "maintenance_category": "Type of work",
    "maintenance_activity": "Work done",
}


def _facts_block(facts: dict[str, dict]) -> dict[str, str]:
    out = {}
    for f, d in facts.items():
        text = d["name"]
        if d.get("description"):
            text += f" ({d['description']})"
        out[FACT_LABELS[f]] = text
    return out


def plausibility_prompt(tax: Taxonomy, full: dict[str, str]) -> str:
    return "Combination to judge:\n" + json.dumps(_facts_block(describe(tax, full)), indent=1, ensure_ascii=False)


def writer_prompt(tax: Taxonomy, shown: list[dict[str, str]], style: str, kind: str) -> str:
    records = [_facts_block(describe(tax, s)) for s in shown]
    hidden = [[FACT_LABELS[f] for f in LABEL_FIELDS if s[f] == UNKNOWN] for s in shown]
    lines = [
        f"Write one work-order text in this style: {STYLES[style]}.",
        "Keep it realistic and short (typically 1-4 lines). Use technician wording, not the formal terms below, "
        "unless a technician would naturally use them. You may include an invented plant-style equipment tag.",
    ]
    if kind == "vague":
        lines.append("The entry is vague and non-specific: it must NOT name a specific failure, part or action.")
    if kind == "multi":
        lines.append(f"The order reports {len(shown)} separate issues on the same equipment; describe each.")
    lines.append(
        "State ONLY the facts listed for each record. For every field listed under 'Do not state', say nothing "
        "about it and do not hint at it."
    )
    payload = {"records": [{"state": r, "do_not_state": h} for r, h in zip(records, hidden)]}
    lines.append("FACTS_JSON: " + json.dumps(payload, ensure_ascii=False))
    lines.append(
        'Reply with JSON: {"text": "<the work-order text>", "records": [{"phrases": {"<field name as given>": '
        '"<exact substring of text that states it>"}}, ...]} with one entry per record, in order.'
    )
    return "\n".join(lines)


def check_plausible(client: LLMClient, model: str, tax: Taxonomy, full: dict[str, str]) -> bool:
    try:
        reply = client.complete(system=PLAUSIBILITY_SYSTEM, user=plausibility_prompt(tax, full), model=model, max_tokens=300)
        return bool(extract_json_object(reply).get("plausible", False))
    except ValueError:
        return False


def write_text(
    client: LLMClient, model: str, tax: Taxonomy, shown: list[dict[str, str]], style: str, kind: str, retries: int = 2
) -> tuple[str, list[dict[str, str]]] | None:
    """Ask the writer for the text and per-record phrases; returns None if it never produced a valid answer."""
    field_by_label = {v: k for k, v in FACT_LABELS.items()}
    for _ in range(retries + 1):
        reply = client.complete(system=WRITER_SYSTEM, user=writer_prompt(tax, shown, style, kind), model=model, max_tokens=1200)
        try:
            obj = extract_json_object(reply)
        except ValueError:
            continue
        text = str(obj.get("text", "")).strip()
        recs = obj.get("records")
        if not (5 <= len(text) <= 1500) or not isinstance(recs, list) or len(recs) != len(shown):
            continue
        phrases = []
        for r, s in zip(recs, shown):
            clean = {}
            for key, span in (r.get("phrases") or {}).items():
                f = field_by_label.get(key, key)
                if f in LABEL_FIELDS and s[f] != UNKNOWN and isinstance(span, str) and span and span in text:
                    clean[f] = span
            phrases.append(clean)
        return text, phrases
    return None


def _all_verified(tax: Taxonomy, full_records: list[dict[str, str]]) -> bool:
    rows = []
    for full in full_records:
        rows.append(tax.classes[full["equipment_class"]])
        rows.append(tax.subunits[full["subunit"]])
        rows.append(tax.items[full["maintainable_item"]])
        for f, table in (
            ("failure_mechanism", tax.mechanisms),
            ("failure_cause", tax.causes),
            ("detection_method", tax.detection_methods),
            ("maintenance_activity", tax.activities),
        ):
            if full[f] != UNKNOWN:
                rows.append(table[full[f]])
        if full["failure_mode"] != UNKNOWN:
            rows.append(next(r for r in tax.failure_modes_by_class[full["equipment_class"]] if r["code"] == full["failure_mode"]))
    return all(r["verified"] for r in rows)


def generate(
    tax: Taxonomy,
    client: LLMClient,
    *,
    model: str,
    total: int,
    test_total: int,
    seed: int,
    weights: dict[str, float],
    floor: int = 3,
    policy: Policy = Policy(),
    max_plausibility_tries: int = 6,
    progress: Callable[[str], None] = lambda s: None,
) -> list[Sample]:
    rng = random.Random(seed)
    class_ids = tax.class_ids_with_data()
    counts = allocate(class_ids, {c: weights.get(c, 1) for c in class_ids}, total, floor)
    test_counts = split_test_counts(counts, test_total)
    style_names = sorted(STYLES)
    samples: list[Sample] = []
    skipped = 0
    for class_id in class_ids:
        for i in range(counts[class_id]):
            kind = pick_kind(rng, policy)
            fulls = None
            for _ in range(max_plausibility_tries):
                first = sample_full_labels(tax, class_id, rng, policy)
                batch = [first]
                if kind == "multi":
                    batch.append(sample_full_labels(tax, class_id, rng, policy, avoid_subunit=first["subunit"]))
                if all(check_plausible(client, model, tax, f) for f in batch):
                    fulls = batch
                    break
            if fulls is None:
                skipped += 1
                progress(f"skipped one {class_id} sample: no plausible combination found")
                continue
            shown = [disclose(f, rng, policy, kind) for f in fulls]
            style = rng.choice(style_names)
            written = write_text(client, model, tax, shown, style, kind)
            if written is None:
                skipped += 1
                progress(f"skipped one {class_id} sample: writer produced no valid text")
                continue
            text, phrases = written
            samples.append(
                Sample(
                    source_id=f"S{seed}-{len(samples) + 1:04d}",
                    split="test" if i < test_counts[class_id] else "dev",
                    kind=kind,
                    style=style,
                    text=text,
                    records=[LabeledRecord(labels=s, phrases=p) for s, p in zip(shown, phrases)],
                    meta={
                        "generator_model": model,
                        "seed": seed,
                        "true_class": class_id,
                        "plausibility_checked": True,
                        "taxonomy_all_verified": _all_verified(tax, fulls),
                    },
                )
            )
        progress(f"{class_id}: done ({len(samples)} samples so far)")
    progress(f"generated {len(samples)} samples, skipped {skipped}")
    return samples
