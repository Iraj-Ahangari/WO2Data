import json

import pytest

from workorder2data.schema import LABEL_FIELDS, UNKNOWN, extract_json_object
from workorder2data.synth.generate import generate, write_text
from workorder2data.taxonomy import Taxonomy


class FakeClient:
    """Stands in for the model: approves plausibility and 'writes' text that states exactly the facts given."""

    def __init__(self, reject_first_n_plausibility=0, bad_writer=False):
        self.reject = reject_first_n_plausibility
        self.bad_writer = bad_writer
        self.calls = []

    def complete(self, *, system, user, model, max_tokens=2000):
        self.calls.append(model)
        if "Combination to judge" in user:
            if self.reject > 0:
                self.reject -= 1
                return '{"plausible": false, "reason": "no"}'
            return '{"plausible": true, "reason": "ok"}'
        if self.bad_writer:
            return "not json"
        payload = json.loads(user.split("FACTS_JSON: ")[1].splitlines()[0])
        texts, recs = [], []
        for r in payload["records"]:
            phrases = {k: v.split(" (")[0] for k, v in r["state"].items()}
            texts.append("; ".join(phrases.values()))
            recs.append({"phrases": phrases})
        return json.dumps({"text": " | ".join(texts) + " noted", "records": recs})


@pytest.fixture(scope="module")
def tax(taxonomy_dir):
    return Taxonomy(taxonomy_dir)


def run(tax, client, **kw):
    n = len(tax.class_ids_with_data())
    args = dict(model="gen-model", total=3 * n, test_total=min(n + 3, 2 * n), seed=7, weights={}, floor=2)
    args.update(kw)
    return generate(tax, client, **args)


def test_generate_produces_valid_gold_labels(tax):
    samples = run(tax, FakeClient())
    assert len(samples) == 3 * len(tax.class_ids_with_data())
    for s in samples:
        assert s.synthetic and s.text
        assert s.meta["generator_model"] == "gen-model"
        for rec in s.records:
            assert set(rec.labels) == set(LABEL_FIELDS)
            assert rec.labels["equipment_class"] in tax.classes or rec.labels["equipment_class"] == UNKNOWN
            for f, span in rec.phrases.items():
                assert rec.labels[f] != UNKNOWN and span in s.text


def test_hidden_fields_are_unknown_and_not_in_phrases(tax):
    for s in run(tax, FakeClient()):
        for rec in s.records:
            assert set(rec.phrases) <= {f for f, v in rec.labels.items() if v != UNKNOWN}


def test_split_sizes_and_ids_are_unique(tax):
    samples = run(tax, FakeClient())
    assert sum(s.split == "test" for s in samples) == min(len(tax.class_ids_with_data()) + 3, 2 * len(tax.class_ids_with_data()))
    assert len({s.source_id for s in samples}) == len(samples)


def test_seed_makes_generation_reproducible(tax):
    a = [s.model_dump() for s in run(tax, FakeClient())]
    b = [s.model_dump() for s in run(tax, FakeClient())]
    assert a == b


def test_implausible_combinations_are_resampled(tax):
    client = FakeClient(reject_first_n_plausibility=3)
    assert len(run(tax, client)) == 3 * len(tax.class_ids_with_data())


def test_unusable_writer_output_skips_the_sample(tax):
    samples = run(tax, FakeClient(bad_writer=True))
    assert samples == []


def test_writer_phrases_not_found_in_text_are_dropped(tax):
    class Liar(FakeClient):
        def complete(self, *, system, user, model, max_tokens=2000):
            out = super().complete(system=system, user=user, model=model, max_tokens=max_tokens)
            if "Combination to judge" in user:
                return out
            obj = extract_json_object(out)
            for r in obj["records"]:
                r["phrases"] = {k: "not in the text at all" for k in r["phrases"]}
            return json.dumps(obj)

    for s in run(tax, Liar()):
        assert all(not rec.phrases for rec in s.records)


def test_write_text_returns_none_after_retries(tax):
    cid = tax.class_ids_with_data()[0]
    shown = [{f: UNKNOWN for f in LABEL_FIELDS} | {"equipment_class": cid}]
    client = FakeClient(bad_writer=True)
    assert write_text(client, "m", tax, shown, "narrative", "single", retries=1) is None
    assert len(client.calls) == 2


def test_extract_json_object_tolerates_fences_and_prose():
    assert extract_json_object('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json_object("nothing here")
