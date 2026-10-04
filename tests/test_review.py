import csv

import pytest

from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Sample
from workorder2data.synth.review import ReviewError, export_csv, import_csv
from workorder2data.synth.sampler import sample_full_labels
from workorder2data.taxonomy import Taxonomy
import random


@pytest.fixture(scope="module")
def tax(taxonomy_dir):
    return Taxonomy(taxonomy_dir)


@pytest.fixture
def samples(tax):
    rng = random.Random(0)
    cid = tax.class_ids_with_data()[0]
    out = []
    for i in range(3):
        labs = [sample_full_labels(tax, cid, rng) for _ in range(2 if i == 1 else 1)]
        out.append(Sample(source_id=f"S{i}", split="test", kind="multi" if i == 1 else "single", style="s",
                          text=f"text {i}", records=[LabeledRecord(labels=l) for l in labs]))
    return out


def edit(path, fn):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    for r in rows:
        fn(r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_export_has_one_row_per_record_with_names(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    assert export_csv(samples, tax, p) == 4
    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert rows[0]["equipment_class"].startswith(samples[0].records[0].labels["equipment_class"])
    assert " | " in rows[0]["equipment_class"]
    assert rows[2]["text"] == "(same text)"


def test_import_applies_only_ok_rows_and_marks_reviewed(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    export_csv(samples, tax, p)
    edit(p, lambda r: r.update(ok="y") if r["source_id"] == "S0" else None)
    applied, reviewed = import_csv(samples, tax, p)
    assert (applied, reviewed) == (1, 1)
    assert samples[0].reviewed and not samples[1].reviewed and not samples[2].reviewed


def test_sample_is_reviewed_only_when_all_its_records_are_ok(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    export_csv(samples, tax, p)
    seen = []
    def only_first_record(r):
        if r["source_id"] == "S1" and r["record"] == "0":
            r["ok"] = "y"
    edit(p, only_first_record)
    import_csv(samples, tax, p)
    assert not samples[1].reviewed


def test_correction_replaces_label_and_drops_its_phrase(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    samples[0].records[0].labels["failure_mechanism"] = next(iter(tax.mechanisms))
    samples[0].records[0].phrases["failure_mechanism"] = "x"
    export_csv(samples, tax, p)
    def fix(r):
        if r["source_id"] == "S0":
            r["ok"] = "y"
            r["failure_mechanism"] = UNKNOWN
    edit(p, fix)
    import_csv(samples, tax, p)
    assert samples[0].records[0].labels["failure_mechanism"] == UNKNOWN
    assert "failure_mechanism" not in samples[0].records[0].phrases


def test_invalid_codes_are_reported_and_nothing_is_applied(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    export_csv(samples, tax, p)
    before = [s.model_dump() for s in samples]
    def bad(r):
        r["ok"] = "y"
        if r["source_id"] == "S0":
            r["failure_cause"] = "99.9"
    edit(p, bad)
    with pytest.raises(ReviewError) as e:
        import_csv(samples, tax, p)
    assert "failure_cause" in str(e.value)
    assert [s.model_dump() for s in samples] == before


def test_unchanged_ok_rows_keep_labels(samples, tax, tmp_path):
    p = tmp_path / "r.csv"
    export_csv(samples, tax, p)
    before = [r.labels for r in samples[0].records]
    edit(p, lambda r: r.update(ok="y"))
    import_csv(samples, tax, p)
    assert [r.labels for r in samples[0].records] == before
    assert all(s.reviewed for s in samples)
    assert set(samples[0].records[0].labels) == set(LABEL_FIELDS)
