"""Two-stage extraction of ISO 14224 records from work-order text.

Stage 1 splits the text into distinct events and picks each event's equipment class.
Stage 2 sees only that class's allowed values and fills the remaining fields.
Deterministic code (tag map, validation, evidence checks) wraps both; a value only survives if it is a valid
taxonomy value AND its evidence is quoted from the text. Anything else becomes `unknown` plus a warning.
"""

from workorder2data.glossary import hints_for
from workorder2data.llm import LLMClient
from workorder2data.records import (
    CLASS_CONFLICT_TAG, CLASS_UNKNOWN, CONFLICTING, EVIDENCE_NOT_FOUND, MODEL_ERROR, NO_TAXONOMY_DATA, NOT_IN_TAXONOMY,
    NOT_STATED, REVIEW_REASONS, UNVERIFIED_TAXONOMY, FieldValue, Record, Warn,
)
from workorder2data.schema import LABEL_FIELDS, MAINTENANCE_CATEGORIES, UNKNOWN, extract_json_object
from workorder2data.tags import TagMap
from workorder2data.taxonomy import Taxonomy

STAGE2_FIELDS = tuple(f for f in LABEL_FIELDS if f != "equipment_class")
CLASS_DEPENDENT = ("subunit", "maintainable_item", "failure_mode")
MAX_EVENTS = 5
FIELD_HELP = {
    "subunit": "the subunit (subsystem) of the equipment that failed or was worked on",
    "maintainable_item": "the specific part (maintainable item) within the subunit",
    "failure_mode": "how the equipment failed or what was observed at equipment level",
    "failure_mechanism": "the physical process behind the failure (how it came about)",
    "failure_cause": "the underlying root cause (design, installation, operation, maintenance, management ...)",
    "detection_method": "how the failure was discovered",
    "maintenance_category": "corrective (work to restore after a failure) or preventive (planned/scheduled work)",
    "maintenance_activity": "the type of maintenance work done",
}

STAGE1_SYSTEM = (
    "You convert free-text CMMS work orders into structured events following ISO 14224. Step 1: split the work order "
    "into distinct events and identify the equipment class of each. An event is one distinct failure or one distinct "
    "maintenance action on one equipment item. Never invent information.\n"
    "Rules:\n"
    "- Choose the equipment class ONLY from the list provided; use \"unknown\" if the text does not make the equipment clear.\n"
    "- class_evidence and event_text must be copied exactly (verbatim) from the work order.\n"
    "- Give at least one event and at most 5. If the text is too vague to identify anything, return one event with class \"unknown\".\n"
    "Reply with JSON only: {\"events\": [{\"equipment_class\": \"<class_id or unknown>\", \"class_evidence\": \"<verbatim quote>\", "
    "\"event_text\": \"<verbatim excerpt describing this event>\"}]}"
)

STAGE2_SYSTEM = (
    "You convert one maintenance event from a CMMS work order into ISO 14224 fields. Never guess: for every field, "
    "choose a value from the allowed list ONLY if the work order states or clearly describes it; otherwise answer "
    "\"unknown\". For each value you give, quote the supporting words EXACTLY (verbatim) from the work order as evidence. "
    "Failure fields (failure mode, mechanism, cause, detection method) apply only if a failure is described; leave "
    "them unknown for pure planned work. Use the abbreviation hints, if any, to understand shorthand, but quote the "
    "original text.\n"
    "Reply with JSON only, one entry per field listed: {\"<field>\": {\"value\": \"<allowed value or unknown>\", "
    "\"evidence\": \"<verbatim quote, empty if unknown>\"}, ...}"
)


class ExtractionError(RuntimeError):
    pass


def _normalise(s: str) -> tuple[str, list[int]]:
    chars, idx, prev_space = [], [], False
    for i, ch in enumerate(s):
        if ch.isspace():
            if prev_space:
                continue
            chars.append(" ")
            prev_space = True
        else:
            chars.append(ch.lower())
            prev_space = False
        idx.append(i)
    return "".join(chars), idx


def find_span(text: str, quote: str) -> str | None:
    """The exact span of `text` matching `quote` (tolerating case and whitespace differences), or None."""
    q = (quote or "").strip()
    if not q:
        return None
    if q in text:
        return q
    nt, idx = _normalise(text)
    nq, _ = _normalise(q)
    nq = nq.strip()
    pos = nt.find(nq) if nq else -1
    if pos < 0:
        return None
    return text[idx[pos] : idx[pos + len(nq) - 1] + 1]


class Extractor:
    def __init__(
        self,
        tax: Taxonomy,
        client: LLMClient,
        *,
        model: str,
        glossary: dict[str, str] | None = None,
        tag_map: TagMap | None = None,
    ):
        self.tax = tax
        self.client = client
        self.model = model
        self.glossary = glossary or {}
        self.tag_map = tag_map or TagMap()
        self._with_data = set(tax.class_ids_with_data())
        self._stage2_cache: dict[str | None, tuple[str, dict[str, dict[str, str]]]] = {}
        self._class_block = "\n".join(
            ["Equipment classes (class_id | name | category):"]
            + [f"{cid} | {c['name']} | {c['category']}" for cid, c in tax.classes.items()]
        )

    # ---------- prompt building ----------
    def _hint_block(self, text: str) -> str:
        hints = hints_for(text, self.glossary)
        return "Abbreviation hints: " + "; ".join(f"{a} = {m}" for a, m in hints) + "\n" if hints else ""

    def stage2_block(self, class_id: str | None) -> tuple[str, dict[str, dict[str, str]]]:
        """(static prompt text, allowed values per field) for a class; class None = class-independent fields only."""
        key = class_id if class_id in self._with_data else None
        if key in self._stage2_cache:
            return self._stage2_cache[key]
        t = self.tax
        allowed: dict[str, dict[str, str]] = {}
        if key:
            allowed["subunit"] = {s["subunit_id"]: s["name"] for s in t.subunits_by_class[key]}
            allowed["maintainable_item"] = {
                i["item_id"]: f'{t.subunits[i["subunit_id"]]["name"]} / {i["name"]}'
                for s in t.subunits_by_class[key] for i in t.items_by_subunit[s["subunit_id"]]
            }
            allowed["failure_mode"] = {}
            for r in t.failure_modes_by_class[key]:
                allowed["failure_mode"].setdefault(r["code"], f'{r["name"]} - {r["examples"]}')
        allowed["failure_mechanism"] = {c: f'{r["name"]}: {r["description"]}' for c, r in t.mechanisms.items() if r["level"] == "2"}
        allowed["failure_cause"] = {c: f'{r["name"]}: {r["description"]}' for c, r in t.causes.items() if r["level"] == "2"}
        allowed["detection_method"] = {c: f'{r["name"]}: {r["description"]}' for c, r in t.detection_methods.items()}
        allowed["maintenance_category"] = {c: c for c in MAINTENANCE_CATEGORIES}
        allowed["maintenance_activity"] = {c: f'{r["name"]}: {r["description"]} (typical use: {r["use"]})' for c, r in t.activities.items()}
        lines = [f"Equipment class: {t.classes[key]['name']}" if key else "Equipment class: not determined"]
        for f in STAGE2_FIELDS:
            if f in allowed:
                lines.append(f"\nField `{f}` - {FIELD_HELP[f]}. Allowed values (value | meaning):")
                lines += [f"{v} | {m}" for v, m in allowed[f].items()]
        self._stage2_cache[key] = ("\n".join(lines), allowed)
        return self._stage2_cache[key]

    def _stage1_user(self, text: str, tag: str | None) -> str:
        return (f"Tag hint: {tag}\n" if tag else "") + self._hint_block(text) + "Work order:\n" + text

    def _stage2_user(self, text: str, event_text: str, tag: str | None) -> str:
        focus = f"\nFocus on this event: {event_text}\n" if event_text and event_text != text else ""
        return (f"Tag hint: {tag}\n" if tag else "") + self._hint_block(text) + "Work order:\n" + text + focus

    def estimate_tokens(self, texts: list[str]) -> tuple[int, int]:
        """Rough (input, output) token estimate for converting `texts` (characters / 4, no API call)."""
        biggest = max((len(self.stage2_block(c)[0]) for c in self._with_data), default=0)
        stage1_static = len(STAGE1_SYSTEM) + len(self._class_block)
        stage2_static = len(STAGE2_SYSTEM) + biggest
        tin = sum((stage1_static + 2 * len(t) + stage2_static) // 4 for t in texts)
        return tin, 500 * len(texts)

    # ---------- model calls ----------
    def _ask(self, static: str, system: str, user: str, max_tokens: int) -> dict:
        for _ in range(2):
            try:
                reply = self.client.complete(system=system, user=user, model=self.model, max_tokens=max_tokens, cached_system=static)
                return extract_json_object(reply)
            except ValueError:
                continue
        raise ExtractionError("model did not return valid JSON")

    # ---------- stage 1 ----------
    def _stage1(self, text: str, tag: str | None) -> list[dict]:
        user = self._stage1_user(text, tag)
        for attempt in range(2):
            obj = self._ask(self._class_block, STAGE1_SYSTEM, user, 1500)
            events = obj.get("events")
            if not isinstance(events, list) or not events:
                continue
            clean, bad = [], []
            for e in events[:MAX_EVENTS]:
                cid = str(e.get("equipment_class", UNKNOWN))
                quote = find_span(text, str(e.get("class_evidence", "")))
                if cid != UNKNOWN and (cid not in self.tax.classes or quote is None):
                    bad.append(f"{cid!r} (class must be a listed class_id and class_evidence must be quoted from the text)")
                    cid_ok, ev = UNKNOWN, None
                else:
                    cid_ok, ev = cid, quote
                clean.append({"class": cid_ok, "evidence": ev,
                              "event_text": find_span(text, str(e.get("event_text", ""))) or "", "rejected": cid if cid != cid_ok else None})
            if bad and attempt == 0:
                user += "\n\nYour previous answer had invalid classes: " + "; ".join(bad) + ". Return the full JSON again; use \"unknown\" if you cannot support a class."
                continue
            return clean
        raise ExtractionError("stage 1 returned no usable events")

    # ---------- stage 2 ----------
    def _stage2(self, text: str, event_text: str, tag: str | None, class_id: str | None):
        static, allowed = self.stage2_block(class_id)
        scope = [f for f in STAGE2_FIELDS if f in allowed]
        user = self._stage2_user(text, event_text, tag)
        user += "\nFields to answer: " + ", ".join(scope)
        results: dict[str, FieldValue] = {}
        problems: dict[str, str] = {}
        for attempt in range(2):
            obj = self._ask(static, STAGE2_SYSTEM, user, 2500)
            results, problems = {}, {}
            for f in scope:
                entry = obj.get(f) or {}
                value = str(entry.get("value", UNKNOWN)) if isinstance(entry, dict) else UNKNOWN
                if value == UNKNOWN or not value:
                    continue
                if value not in allowed[f]:
                    problems[f] = NOT_IN_TAXONOMY
                    continue
                span = find_span(text, str(entry.get("evidence", "")))
                if span is None:
                    problems[f] = EVIDENCE_NOT_FOUND
                    continue
                results[f] = FieldValue(value=value, evidence=span)
            if not problems or attempt == 1:
                break
            user += ("\n\nYour previous answer had problems: " + "; ".join(f"{f}: {r}" for f, r in problems.items())
                     + ". Return the full JSON again. Use only allowed values, quote evidence exactly from the work order, or answer unknown.")
        return results, problems, scope

    # ---------- assembly ----------
    def _lookup(self, field: str, value: str, class_id: str | None) -> tuple[str | None, bool | None]:
        t = self.tax
        row = None
        if field == "equipment_class":
            row = t.classes.get(value)
        elif field == "subunit":
            row = t.subunits.get(value)
        elif field == "maintainable_item":
            row = t.items.get(value)
        elif field == "failure_mode" and class_id:
            row = next((r for r in t.failure_modes_by_class.get(class_id, []) if r["code"] == value), None)
        elif field == "failure_mechanism":
            row = t.mechanisms.get(value)
        elif field == "failure_cause":
            row = t.causes.get(value)
        elif field == "detection_method":
            row = t.detection_methods.get(value)
        elif field == "maintenance_activity":
            row = t.activities.get(value)
        elif field == "maintenance_category":
            return value, None
        return (row["name"], row["verified"]) if row else (None, None)

    def convert(self, text: str, *, source_id: str = "wo", tag: str | None = None, source_date: str | None = None) -> list[Record]:
        try:
            events = self._stage1(text, tag)
        except ExtractionError as e:
            return [self._failed(source_id, 1, tag, source_date, str(e))]
        records = []
        for n, ev in enumerate(events, start=1):
            records.append(self._record(text, source_id, n, ev, tag, source_date))
        return records

    def _failed(self, source_id: str, n: int, tag, source_date, msg: str) -> Record:
        r = Record(record_id=f"{source_id}:{n}", source_id=source_id, tag=tag, source_date=source_date,
                   fields={f: FieldValue() for f in LABEL_FIELDS})
        r.warnings.append(Warn(reason=MODEL_ERROR, detail=msg))
        r.needs_review = True
        r.meta = {"model": self.model}
        return r

    def _record(self, text: str, source_id: str, n: int, ev: dict, tag: str | None, source_date: str | None) -> Record:
        warnings: list[Warn] = []
        found = self.tag_map.find(ev["event_text"]) if ev["event_text"] else None
        found = found or self.tag_map.find(text)
        rec_tag = found[0] if found else tag
        tag_class = self.tag_map.class_for(found[1]) if found else None
        class_id = ev["class"]
        evidence = ev["evidence"]
        if ev.get("rejected"):
            warnings.append(Warn(field="equipment_class", reason=EVIDENCE_NOT_FOUND, detail=f"model proposed {ev['rejected']!r} without usable evidence"))
        if tag_class:
            if class_id != UNKNOWN and class_id != tag_class:
                warnings.append(Warn(field="equipment_class", reason=CLASS_CONFLICT_TAG, detail=f"text suggests {class_id}, tag prefix maps to {tag_class}"))
            class_id, evidence = tag_class, rec_tag
        fields = {f: FieldValue() for f in LABEL_FIELDS}
        if class_id != UNKNOWN:
            name, ver = self._lookup("equipment_class", class_id, None)
            fields["equipment_class"] = FieldValue(value=class_id, name=name, evidence=evidence, taxonomy_verified=ver)
        else:
            warnings.append(Warn(field="equipment_class", reason=CLASS_UNKNOWN))
        stage_class = class_id if class_id != UNKNOWN else None
        try:
            results, problems, scope = self._stage2(text, ev["event_text"], rec_tag, stage_class)
        except ExtractionError as e:
            warnings.append(Warn(reason=MODEL_ERROR, detail=str(e)))
            results, problems, scope = {}, {}, []
        for f, v in results.items():
            v.name, v.taxonomy_verified = self._lookup(f, v.value, stage_class)
            fields[f] = v
        # an item names its subunit: keep them consistent
        item, sub = fields["maintainable_item"], fields["subunit"]
        if item.value != UNKNOWN:
            item_sub = self.tax.items[item.value]["subunit_id"]
            if sub.value == UNKNOWN:
                name, ver = self._lookup("subunit", item_sub, stage_class)
                fields["subunit"] = FieldValue(value=item_sub, name=name, evidence=item.evidence, taxonomy_verified=ver)
            elif sub.value != item_sub:
                fields["maintainable_item"] = FieldValue()
                warnings.append(Warn(field="maintainable_item", reason=CONFLICTING, detail="item does not belong to the chosen subunit"))
        if class_id != UNKNOWN and stage_class is None:
            warnings.append(Warn(reason=NO_TAXONOMY_DATA, detail=f"no subunit/failure-mode data for class {class_id}"))
        for f in LABEL_FIELDS:
            if f in problems:
                warnings.append(Warn(field=f, reason=problems[f]))
            elif fields[f].value == UNKNOWN and f != "equipment_class":
                out_of_scope = f in CLASS_DEPENDENT and f not in scope
                warnings.append(Warn(field=f, reason=(NO_TAXONOMY_DATA if class_id != UNKNOWN else CLASS_UNKNOWN) if out_of_scope else NOT_STATED))
        unverified = [f for f, v in fields.items() if v.taxonomy_verified is False]
        if unverified:
            warnings.append(Warn(reason=UNVERIFIED_TAXONOMY, detail=",".join(unverified)))
        need = (
            fields["equipment_class"].value == UNKNOWN
            or any(w.reason in REVIEW_REASONS for w in warnings)
            or (fields["failure_mode"].value == UNKNOWN and fields["maintenance_activity"].value == UNKNOWN)
        )
        return Record(record_id=f"{source_id}:{n}", source_id=source_id, tag=rec_tag, source_date=source_date,
                      fields=fields, warnings=warnings, needs_review=need, meta={"model": self.model})
