import csv
import json

import pytest

import workorder2data.cli as cli
from conftest import FIXTURE_DIR
from test_extractor import Script, ev, s2
from workorder2data.cli import main
from workorder2data.extractor import Extractor
from workorder2data.records import Record
from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Prediction, Sample, read_jsonl, write_jsonl
from workorder2data.taxonomy import Taxonomy


@pytest.fixture
def fake(monkeypatch):
    script = Script(lambda n, user: ev(text=None) if False else {"events": [{"equipment_class": "example_widgets", "class_evidence": "seal", "event_text": "seal"}]},
                    s2(failure_mode=("AAA", "leaking")))
    monkeypatch.setattr(cli, "make_extractor", lambda **kw: Extractor(Taxonomy(FIXTURE_DIR), script, model="ext-model"))
    monkeypatch.delenv("W2D_GENERATOR_MODEL", raising=False)
    monkeypatch.delenv("W2D_EXTRACTOR_MODEL", raising=False)
    return script


def test_convert_single_text_prints_records(fake, capsys):
    assert main(["convert", "seal leaking badly"]) == 0
    out = capsys.readouterr().out
    assert "wo:1" in out and "failure_mode" in out and "leaking" in out


def test_convert_json_output_is_valid(fake, capsys):
    assert main(["convert", "seal leaking badly", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["fields"]["failure_mode"]["value"] == "AAA"


def test_dry_run_makes_no_calls(fake, capsys):
    assert main(["convert", "seal leaking", "--dry-run"]) == 0
    assert "no API calls were made" in capsys.readouterr().out and fake.calls == []


def test_batch_csv_writes_all_outputs_and_summary(fake, tmp_path, capsys):
    src = tmp_path / "wo.csv"
    src.write_text("wo,desc,note\n1,seal leaking,replaced\n2,seal weeping,\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["batch", str(src), "--id-col", "wo", "--text-col", "desc,note", "--out-dir", str(out)]) == 0
    text = capsys.readouterr().out
    assert "2 work order(s) -> 2 record(s)" in text and "unknown fields" in text
    recs = read_jsonl(out / "records.jsonl", Record)
    assert [r.source_id for r in recs] == ["1", "2"]
    assert len(list(csv.DictReader(open(out / "records.csv", newline="", encoding="utf-8")))) == 2
    assert [p.source_id for p in read_jsonl(out / "predictions.jsonl", Prediction)] == ["1", "2"]


def test_batch_resume_skips_finished_orders(fake, tmp_path):
    src = tmp_path / "wo.csv"
    src.write_text("wo,desc\n1,seal leaking\n2,seal weeping\n", encoding="utf-8")
    out = tmp_path / "out"
    args = ["batch", str(src), "--id-col", "wo", "--text-col", "desc", "--out-dir", str(out)]
    main(args)
    calls_before = len(fake.calls)
    main(args + ["--resume"])
    assert len(fake.calls) == calls_before
    assert len(read_jsonl(out / "records.jsonl", Record)) == 2


def test_batch_csv_without_column_options_is_a_clear_error(fake, tmp_path, capsys):
    src = tmp_path / "wo.csv"
    src.write_text("wo,desc\n1,x\n", encoding="utf-8")
    assert main(["batch", str(src)]) == 2
    assert "--id-col" in capsys.readouterr().err


def test_batch_text_file_and_folder(fake, tmp_path, capsys):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.txt").write_text("seal leaking\n---\nseal weeping", encoding="utf-8")
    (tmp_path / "in" / "b.txt").write_text("seal noisy", encoding="utf-8")
    assert main(["batch", str(tmp_path / "in"), "--out-dir", str(tmp_path / "o")]) == 0
    assert "3 work order(s)" in capsys.readouterr().out


def test_extractor_model_must_differ_from_generator(fake, capsys, monkeypatch):
    monkeypatch.setenv("W2D_GENERATOR_MODEL", "same")
    assert main(["batch", "x.txt", "--model", "same"]) == 2
    assert "must differ" in capsys.readouterr().err


def test_predict_then_eval_closes_the_loop(fake, tmp_path, capsys):
    recs = [LabeledRecord(labels={f: UNKNOWN for f in LABEL_FIELDS} | {"equipment_class": "example_widgets", "failure_mode": "AAA"})]
    samples = tmp_path / "s.jsonl"
    write_jsonl(samples, [Sample(source_id="S1", split="test", kind="single", style="s", text="seal leaking", records=recs,
                                 meta={"generator_model": "gen-model"})])
    preds = tmp_path / "p.jsonl"
    assert main(["predict", "--samples", str(samples), "--out", str(preds)]) == 0
    assert main(["eval", "--gold", str(samples), "--pred", str(preds)]) == 0
    out = capsys.readouterr().out
    assert "failure_mode" in out and "100.0" in out
