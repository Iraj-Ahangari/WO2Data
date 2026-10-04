import csv
import json

import pytest

from workorder2data.glossary import hints_for, load_glossary
from workorder2data.inputs import CsvMapping, read_csv, read_text_file, split_text
from workorder2data.records import FieldValue, Record, Warn, to_predictions, write_records_csv, write_records_jsonl
from workorder2data.schema import LABEL_FIELDS, UNKNOWN, Prediction, read_jsonl
from workorder2data.tags import TagMap, load_tag_map


def test_csv_mapping_combines_text_columns_and_passes_through(tmp_path):
    p = tmp_path / "wo.csv"
    p.write_text("wo,desc,comments,tag,created\n"
                 "100,seal leaking,replaced seal,P-101,2026-01-02\n"
                 "101,,,P-102,2026-01-03\n"
                 ",noisy fan,,,\n", encoding="utf-8")
    m = CsvMapping("wo", ["desc", "comments"], tag_col="tag", date_col="created")
    orders = read_csv(p, m)
    assert [o.source_id for o in orders] == ["100", "wo#row4"]
    assert orders[0].text == "Tag: P-101\ndesc: seal leaking\ncomments: replaced seal"
    assert orders[0].tag == "P-101" and orders[0].source_date == "2026-01-02"
    assert orders[1].text == "desc: noisy fan"      # empty rows are skipped


def test_csv_missing_column_is_a_clear_error(tmp_path):
    p = tmp_path / "wo.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="column"):
        read_csv(p, CsvMapping("a", ["zzz"]))


def test_text_file_splits_on_dashes_only(tmp_path):
    one = tmp_path / "one.txt"
    one.write_text("seal leaking\nreplaced seal\n", encoding="utf-8")
    many = tmp_path / "many.txt"
    many.write_text("first order\n---\nsecond order\n --- \nthird\n", encoding="utf-8")
    assert [o.source_id for o in read_text_file(one)] == ["one"]
    assert [o.source_id for o in read_text_file(many)] == ["many#1", "many#2", "many#3"]
    assert split_text("a\n---\n\n---\n") == ["a"]


def test_glossary_loads_and_matches_whole_words(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("abbreviation,meaning\nbrg,bearing\nmech seal,mechanical seal\n", encoding="utf-8")
    g = load_glossary(p)
    assert hints_for("Brg noisy; MECH SEAL ok", g) == [("brg", "bearing"), ("mech seal", "mechanical seal")]
    assert hints_for("brgs and subrg", g) == []
    assert load_glossary(None) == {}


def test_tag_map_finds_mapped_prefix_first(tmp_path):
    tm = TagMap(prefixes={"P": "pump_class"})
    assert tm.find("fan FN-12 near P-101A pump") == ("P-101A", "P")
    assert tm.class_for("P") == "pump_class" and tm.class_for("FN") is None and tm.class_for(None) is None
    assert TagMap().find("no tag here") is None


def test_tag_map_yaml_validates_class_ids(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("prefixes:\n  P: class_a\n  X: nope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown equipment class ids"):
        load_tag_map(p, {"class_a"})
    p.write_text("tag_pattern: '\\b([A-Z]{2})/(\\d+)\\b'\nprefixes:\n  PU: class_a\n", encoding="utf-8")
    tm = load_tag_map(p, {"class_a"})
    assert tm.find("see PU/7") == ("PU/7", "PU")


def _record(**vals):
    f = {k: FieldValue() for k in LABEL_FIELDS}
    for k, v in vals.items():
        f[k] = FieldValue(value=v, name=f"name of {v}", evidence="ev")
    return Record(record_id="W:1", source_id="W", tag="P-1", source_date="2026-01-01", fields=f,
                  warnings=[Warn(field="failure_cause", reason="not_stated")], needs_review=True)


def test_csv_has_blank_cells_for_unknowns_and_warnings(tmp_path):
    p = tmp_path / "r.csv"
    write_records_csv(p, [_record(equipment_class="class_a", failure_mode="AAA")])
    row = next(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert row["equipment_class"] == "class_a" and row["equipment_class_name"] == "name of class_a"
    assert row["failure_cause"] == "" and row["needs_review"] == "yes"
    assert "failure_cause:not_stated" in row["warnings"] and row["tag"] == "P-1"


def test_jsonl_round_trip_and_predictions(tmp_path):
    p = tmp_path / "r.jsonl"
    recs = [_record(equipment_class="class_a"), _record(failure_mode="AAA")]
    recs[1].record_id = "W:2"
    write_records_jsonl(p, recs)
    back = read_jsonl(p, Record)
    assert back[0].fields["equipment_class"].evidence == "ev"
    [pred] = to_predictions(back, model="m")
    assert isinstance(pred, Prediction) and pred.source_id == "W" and pred.meta == {"model": "m"}
    assert [r.value("equipment_class") for r in pred.records] == ["class_a", UNKNOWN]
