import json
import re

import pytest

from conftest import FIXTURE_DIR
from workorder2data.extractor import Extractor, find_span
from workorder2data.records import (
    CLASS_CONFLICT_TAG, CLASS_UNKNOWN, CONFLICTING, EVIDENCE_NOT_FOUND, MODEL_ERROR, NO_TAXONOMY_DATA, NOT_IN_TAXONOMY,
    NOT_STATED, UNVERIFIED_TAXONOMY,
)
from workorder2data.schema import LABEL_FIELDS, UNKNOWN
from workorder2data.tags import TagMap
from workorder2data.taxonomy import Taxonomy

TEXT = "P-101 mech seal leaking, found worn faces, replaced seal"


class Script:
    """Fake model: `stage1` / `stage2` are callables (or constants) producing the reply for each stage."""

    def __init__(self, stage1, stage2):
        self.stage1, self.stage2, self.calls = stage1, stage2, []

    def complete(self, *, system, user, model, max_tokens=2000, cached_system=None):
        stage = 1 if "Step 1" in system else 2
        self.calls.append({"stage": stage, "system": system, "cached": cached_system, "user": user})
        n = sum(c["stage"] == stage for c in self.calls)
        h = self.stage1 if stage == 1 else self.stage2
        out = h(n, user) if callable(h) else h
        return out if isinstance(out, str) else json.dumps(out)


def ev(cls="example_widgets", cls_ev="mech seal", text=TEXT):
    return {"events": [{"equipment_class": cls, "class_evidence": cls_ev, "event_text": text}]}


def s2(**fields):
    base = {f: {"value": "unknown", "evidence": ""} for f in
            ("subunit", "maintainable_item", "failure_mode", "failure_mechanism", "failure_cause",
             "detection_method", "maintenance_category", "maintenance_activity")}
    for f, (v, e) in fields.items():
        base[f] = {"value": v, "evidence": e}
    return base


@pytest.fixture(scope="module")
def tax():
    return Taxonomy(FIXTURE_DIR)


def make(tax, script, **kw):
    return Extractor(tax, script, model="ext-model", **kw)


def test_happy_path_fills_fields_with_verbatim_evidence(tax):
    script = Script(ev(), s2(subunit=("example_widgets.1", "mech seal"), failure_mode=("AAA", "leaking"),
                              maintenance_activity=("1", "replaced seal"), maintenance_category=("corrective", "leaking")))
    [rec] = make(tax, script).convert(TEXT, source_id="W1")
    f = rec.fields
    assert f["equipment_class"].value == "example_widgets" and f["equipment_class"].evidence == "mech seal"
    assert f["failure_mode"].value == "AAA" and f["failure_mode"].evidence == "leaking"
    assert f["failure_mode"].name == "Example failure mode"
    assert f["failure_mechanism"].value == UNKNOWN
    assert all(v.evidence is None or v.evidence in TEXT for v in f.values())
    assert any(w.field == "failure_mechanism" and w.reason == NOT_STATED for w in rec.warnings)
    assert any(w.reason == UNVERIFIED_TAXONOMY for w in rec.warnings)
    assert not rec.needs_review and rec.record_id == "W1:1" and rec.meta["model"] == "ext-model"


def test_evidence_not_in_text_is_retried_then_rejected(tax):
    script = Script(ev(), s2(failure_mode=("AAA", "gearbox fire")))
    [rec] = make(tax, script).convert(TEXT)
    assert rec.fields["failure_mode"].value == UNKNOWN
    assert any(w.field == "failure_mode" and w.reason == EVIDENCE_NOT_FOUND for w in rec.warnings)
    assert rec.needs_review
    assert sum(c["stage"] == 2 for c in script.calls) == 2   # one retry


def test_invalid_value_is_corrected_by_the_retry(tax):
    def stage2(n, user):
        return s2(failure_mode=("ZZZ" if n == 1 else "AAA", "leaking"))
    script = Script(ev(), stage2)
    [rec] = make(tax, script).convert(TEXT)
    assert rec.fields["failure_mode"].value == "AAA"
    assert "previous answer had problems" in script.calls[-1]["user"]
    assert not any(w.reason == NOT_IN_TAXONOMY for w in rec.warnings)


def test_invalid_value_after_retry_becomes_unknown_with_warning(tax):
    [rec] = make(tax, Script(ev(), s2(failure_mode=("ZZZ", "leaking")))).convert(TEXT)
    assert rec.fields["failure_mode"].value == UNKNOWN
    assert any(w.field == "failure_mode" and w.reason == NOT_IN_TAXONOMY for w in rec.warnings)


def test_class_without_taxonomy_data_keeps_class_and_blanks_dependent_fields(tax):
    script = Script(ev(cls="example_gadgets", cls_ev="mech seal"), s2(failure_mechanism=("1.1", "worn")))
    [rec] = make(tax, script).convert(TEXT)
    assert rec.fields["equipment_class"].value == "example_gadgets"
    assert all(rec.fields[f].value == UNKNOWN for f in ("subunit", "maintainable_item", "failure_mode"))
    assert any(w.reason == NO_TAXONOMY_DATA for w in rec.warnings) and rec.needs_review
    stage2_prompt = [c for c in script.calls if c["stage"] == 2][0]["cached"]
    assert "failure_mode" not in stage2_prompt and "failure_mechanism" in stage2_prompt


def test_unknown_class_gives_warning_and_review(tax):
    [rec] = make(tax, Script(ev(cls="unknown", cls_ev=""), s2())).convert("checked it, ok")
    assert rec.fields["equipment_class"].value == UNKNOWN
    assert any(w.reason == CLASS_UNKNOWN for w in rec.warnings) and rec.needs_review


def test_invented_class_is_rejected(tax):
    [rec] = make(tax, Script(ev(cls="nonexistent_class"), s2())).convert(TEXT)
    assert rec.fields["equipment_class"].value == UNKNOWN
    assert any(w.reason == EVIDENCE_NOT_FOUND and w.field == "equipment_class" for w in rec.warnings)


def test_tag_prefix_map_overrides_model_class_and_warns(tax):
    tm = TagMap(prefixes={"P": "example_widgets"})
    [rec] = make(tax, Script(ev(cls="example_gadgets"), s2()), tag_map=tm).convert(TEXT)
    assert rec.fields["equipment_class"].value == "example_widgets"
    assert rec.fields["equipment_class"].evidence == "P-101" and rec.tag == "P-101"
    assert any(w.reason == CLASS_CONFLICT_TAG for w in rec.warnings)


def test_tag_map_fills_class_when_model_cannot(tax):
    tm = TagMap(prefixes={"P": "example_widgets"})
    [rec] = make(tax, Script(ev(cls="unknown", cls_ev=""), s2()), tag_map=tm).convert(TEXT)
    assert rec.fields["equipment_class"].value == "example_widgets"
    assert not any(w.reason in (CLASS_CONFLICT_TAG, CLASS_UNKNOWN) for w in rec.warnings)


def test_multiple_events_become_multiple_records(tax):
    two = {"events": [{"equipment_class": "example_widgets", "class_evidence": "seal", "event_text": "seal leaking"},
                      {"equipment_class": "example_widgets", "class_evidence": "bearing", "event_text": "bearing noisy"}]}
    recs = make(tax, Script(two, s2())).convert("seal leaking and bearing noisy", source_id="W2")
    assert [r.record_id for r in recs] == ["W2:1", "W2:2"]


def test_item_without_subunit_derives_the_subunit(tax):
    [rec] = make(tax, Script(ev(), s2(maintainable_item=("example_widgets.2#1", "seal")))).convert(TEXT)
    assert rec.fields["subunit"].value == "example_widgets.2"
    assert rec.fields["subunit"].evidence == "seal"


def test_item_in_another_subunit_is_dropped_as_conflicting(tax):
    [rec] = make(tax, Script(ev(), s2(subunit=("example_widgets.1", "mech seal"), maintainable_item=("example_widgets.2#1", "seal")))).convert(TEXT)
    assert rec.fields["maintainable_item"].value == UNKNOWN
    assert any(w.reason == CONFLICTING for w in rec.warnings)


def test_glossary_hints_only_for_present_abbreviations_and_text_untouched(tax):
    script = Script(ev(), s2())
    ex = make(tax, script, glossary={"brg": "bearing", "lkg": "leaking"})
    ex.convert("P-101 brg noisy")
    user = script.calls[0]["user"]
    assert "brg = bearing" in user and "lkg" not in user and "P-101 brg noisy" in user


def test_garbage_model_output_gives_a_flagged_empty_record(tax):
    [rec] = make(tax, Script("not json at all", s2())).convert(TEXT)
    assert rec.needs_review and any(w.reason == MODEL_ERROR for w in rec.warnings)
    assert all(v.value == UNKNOWN for v in rec.fields.values())


def test_all_label_fields_present_on_every_record(tax):
    [rec] = make(tax, Script(ev(), s2())).convert(TEXT)
    assert set(rec.fields) == set(LABEL_FIELDS)


def test_find_span_tolerates_case_and_whitespace_and_returns_original():
    assert find_span("Seal   LEAKING\nbadly", "seal leaking") == "Seal   LEAKING"
    assert find_span("abc", "xyz") is None and find_span("abc", "  ") is None


def test_estimate_tokens_makes_no_calls_and_grows_with_input(tax):
    script = Script(ev(), s2())
    ex = make(tax, script)
    small = ex.estimate_tokens(["a"])[0]
    assert ex.estimate_tokens(["a" * 4000])[0] > small and script.calls == []
