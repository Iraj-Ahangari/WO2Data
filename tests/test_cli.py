import json

import pytest

from workorder2data.cli import main
from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Prediction, Sample, read_jsonl, write_jsonl


def test_dry_run_makes_no_api_calls_and_prints_allocation(capsys, taxonomy_dir, monkeypatch):
    monkeypatch.setenv("W2D_TAXONOMY_DIR", str(taxonomy_dir))
    import workorder2data.taxonomy as t
    import workorder2data.cli as cli
    monkeypatch.setattr(t, "TAXONOMY_DIR", taxonomy_dir)
    monkeypatch.setattr(cli, "Taxonomy", lambda: t.Taxonomy(taxonomy_dir))
    n = len(t.Taxonomy(taxonomy_dir).class_ids_with_data())
    code = main(["synth", "generate", "--dry-run", "--total", str(3 * n), "--test", str(min(n + 1, 2 * n)), "--floor", "2"])
    out = capsys.readouterr().out
    assert code == 0 and "no API calls were made" in out and "estimated calls" in out


def test_generator_and_extractor_models_must_differ(capsys, monkeypatch):
    monkeypatch.setenv("W2D_EXTRACTOR_MODEL", "same-model")
    code = main(["synth", "generate", "--dry-run", "--model", "same-model"])
    assert code == 2 and "must differ" in capsys.readouterr().err


def test_eval_command_scores_an_oracle(tmp_path, capsys):
    recs = [LabeledRecord(labels={f: UNKNOWN for f in LABEL_FIELDS} | {"equipment_class": "class_a", "failure_mode": "AAA"})]
    gold = [Sample(source_id="1", split="test", kind="single", style="s", text="t", records=recs)]
    g, p, j = tmp_path / "g.jsonl", tmp_path / "p.jsonl", tmp_path / "r.json"
    write_jsonl(g, gold)
    write_jsonl(p, [Prediction(source_id="1", records=recs)])
    assert main(["eval", "--gold", str(g), "--pred", str(p), "--split", "test", "--json", str(j)]) == 0
    assert "failure_mode" in capsys.readouterr().out
    assert json.loads(j.read_text())["overall"]["precision"] == 1.0
    assert read_jsonl(g, Sample)[0].source_id == "1"


def test_missing_taxonomy_gives_a_clear_error(capsys, monkeypatch, tmp_path):
    import workorder2data.cli as cli
    import workorder2data.taxonomy as t
    monkeypatch.setattr(cli, "Taxonomy", lambda: t.Taxonomy(tmp_path))
    assert main(["synth", "generate", "--dry-run"]) == 2
    assert "not distributed with this repo" in capsys.readouterr().err
