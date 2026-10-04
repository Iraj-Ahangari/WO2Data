from workorder2data.evaluate import Counts, align, evaluate, format_report, report_to_dict
from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Prediction, Sample


def rec(**kw):
    return LabeledRecord(labels={f: kw.get(f, UNKNOWN) for f in LABEL_FIELDS})


def sample(sid, records, split="test", **meta):
    return Sample(source_id=sid, split=split, kind="single", style="s", text="t", records=records, meta=meta)


def pred(sid, records, **meta):
    return Prediction(source_id=sid, records=records, meta=meta)


def test_counts_semantics():
    c = Counts()
    c.add("a", "a")          # correct
    c.add("a", "b")          # wrong
    c.add(UNKNOWN, "b")      # hallucinated
    c.add("a", UNKNOWN)      # missed
    c.add(UNKNOWN, UNKNOWN)  # abstained correctly: not counted
    assert (c.correct, c.wrong, c.hallucinated, c.missed) == (1, 1, 1, 1)
    assert c.precision == 1 / 3 and c.recall == 1 / 3


def test_oracle_predictions_score_perfectly():
    gold = [sample("1", [rec(equipment_class="class_a", failure_mode="AAA")]),
            sample("2", [rec(equipment_class="class_b", maintenance_activity="1")])]
    preds = [pred(s.source_id, s.records) for s in gold]
    r = evaluate(gold, preds)
    o = r.overall()
    assert o.precision == 1.0 and o.recall == 1.0 and o.wrong == o.hallucinated == o.missed == 0


def test_all_unknown_predictions_have_zero_recall_and_no_precision_penalty():
    gold = [sample("1", [rec(equipment_class="class_a", failure_mode="AAA")])]
    r = evaluate(gold, [pred("1", [rec()])])
    assert r.fields["equipment_class"].missed == 1
    assert r.fields["equipment_class"].recall == 0.0
    assert r.fields["equipment_class"].precision is None


def test_guessing_a_hidden_field_counts_as_hallucination():
    gold = [sample("1", [rec(equipment_class="class_a")])]
    r = evaluate(gold, [pred("1", [rec(equipment_class="class_a", failure_cause="9.9")])])
    assert r.fields["failure_cause"].hallucinated == 1
    assert r.fields["failure_cause"].precision == 0.0


def test_missing_prediction_is_scored_as_missed_with_warning():
    gold = [sample("1", [rec(equipment_class="class_a")])]
    r = evaluate(gold, [])
    assert r.fields["equipment_class"].missed == 1
    assert any("no prediction" in w for w in r.warnings)


def test_align_pairs_multi_failure_records_by_content_not_order():
    gold = [rec(equipment_class="class_a", failure_mode="AAA"), rec(equipment_class="class_a", failure_mode="BBB")]
    pred_records = [rec(equipment_class="class_a", failure_mode="BBB"), rec(equipment_class="class_a", failure_mode="AAA")]
    assert sorted(align(gold, pred_records)) == [(0, 1), (1, 0)]


def test_extra_and_missing_records_are_unmatched():
    gold = [rec(equipment_class="class_a", failure_mode="AAA"), rec(equipment_class="class_a", failure_mode="BBB")]
    s = [sample("1", gold)]
    r = evaluate(s, [pred("1", [gold[0], rec(equipment_class="class_b", failure_mode="CCC")])])
    assert r.n_unmatched_gold == 1 and r.n_unmatched_pred == 1
    assert r.fields["failure_mode"].missed == 1 and r.fields["failure_mode"].hallucinated == 1


def test_split_and_reviewed_filters():
    g = [sample("1", [rec(equipment_class="class_a")], split="test"), sample("2", [rec(equipment_class="class_a")], split="dev")]
    g[0].reviewed = True
    preds = [pred(s.source_id, s.records) for s in g]
    assert evaluate(g, preds, split="test").n_samples == 1
    assert evaluate(g, preds, reviewed_only=True).n_samples == 1
    assert evaluate(g, preds).n_samples == 2


def test_warns_when_extractor_model_is_the_generator_model():
    g = [sample("1", [rec(equipment_class="class_a")], generator_model="m1")]
    r = evaluate(g, [pred("1", g[0].records, model="m1")])
    assert any("also generated" in w for w in r.warnings)
    assert not evaluate(g, [pred("1", g[0].records, model="m2")]).warnings


def test_report_formats_and_serialises():
    g = [sample("1", [rec(equipment_class="class_a", failure_mode="AAA")], true_class="class_a")]
    r = evaluate(g, [pred("1", g[0].records)])
    text = format_report(r, min_class_known=1)
    assert "failure_mode" in text and "class_a" in text
    assert report_to_dict(r)["overall"]["precision"] == 1.0
