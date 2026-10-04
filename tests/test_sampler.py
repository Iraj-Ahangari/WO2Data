import random

import pytest

from workorder2data.schema import LABEL_FIELDS, UNKNOWN
from workorder2data.synth.sampler import (
    Policy,
    allocate,
    describe,
    disclose,
    sample_full_labels,
    split_test_counts,
)
from workorder2data.taxonomy import Taxonomy


@pytest.fixture(scope="module")
def tax(taxonomy_dir):
    return Taxonomy(taxonomy_dir)


def test_allocate_respects_floor_total_and_weights():
    ids = ["a", "b", "c", "d"]
    counts = allocate(ids, {"a": 3, "b": 1, "c": 1, "d": 1}, total=20, floor=3)
    assert sum(counts.values()) == 20
    assert all(v >= 3 for v in counts.values())
    assert counts["a"] > counts["b"]


def test_allocate_rejects_total_below_floor():
    with pytest.raises(ValueError):
        allocate(["a", "b"], {"a": 1, "b": 1}, total=5, floor=3)


def test_split_test_counts_sum_and_per_class_bounds():
    counts = allocate([f"c{i}" for i in range(27)], {f"c{i}": 3 if i < 5 else 1 for i in range(27)}, 200, 3)
    test = split_test_counts(counts, 60)
    assert sum(test.values()) == 60
    assert all(1 <= test[c] <= counts[c] - 1 for c in counts)


def test_sampled_labels_are_taxonomy_valid(tax):
    rng = random.Random(1)
    for class_id in tax.class_ids_with_data():
        for _ in range(20):
            lab = sample_full_labels(tax, class_id, rng)
            assert set(lab) == set(LABEL_FIELDS)
            assert lab["equipment_class"] == class_id
            sub = tax.subunits[lab["subunit"]]
            assert sub["class_id"] == class_id
            assert tax.items[lab["maintainable_item"]]["subunit_id"] == lab["subunit"]
            letter = {"corrective": "C", "preventive": "P"}[lab["maintenance_category"]]
            assert letter in tax.activities[lab["maintenance_activity"]]["use"].split(";")
            if lab["failure_mode"] != UNKNOWN:
                assert lab["failure_mode"] in {r["code"] for r in tax.failure_modes_by_class[class_id]}
                assert lab["failure_mechanism"] in tax.mechanisms
                assert lab["failure_cause"] in tax.causes
                assert lab["detection_method"] in tax.detection_methods
            else:
                assert lab["maintenance_category"] == "preventive"


def test_sampling_is_deterministic_for_a_seed(tax):
    cid = tax.class_ids_with_data()[0]
    assert sample_full_labels(tax, cid, random.Random(5)) == sample_full_labels(tax, cid, random.Random(5))


def test_generic_entries_are_never_sampled(tax):
    rng = random.Random(2)
    cid = tax.class_ids_with_data()[0]
    policy = Policy()
    for _ in range(100):
        lab = sample_full_labels(tax, cid, rng, policy)
        for code, table in ((lab["failure_mechanism"], tax.mechanisms), (lab["failure_cause"], tax.causes)):
            if code != UNKNOWN:
                name = table[code]["name"].lower()
                assert name not in policy.skip_names and "general" not in name


def test_disclose_hides_fields_as_unknown_and_keeps_item_with_subunit(tax):
    rng = random.Random(3)
    cid = tax.class_ids_with_data()[0]
    policy = Policy()
    for _ in range(50):
        full = sample_full_labels(tax, cid, rng, policy)
        shown = disclose(full, rng, policy, "single")
        assert set(shown) == set(LABEL_FIELDS)
        for f, v in shown.items():
            assert v in (UNKNOWN, full[f])
        assert sum(v != UNKNOWN for v in shown.values()) >= 2
        if shown["maintainable_item"] != UNKNOWN:
            assert shown["subunit"] != UNKNOWN


def test_vague_records_reveal_little(tax):
    rng = random.Random(4)
    cid = tax.class_ids_with_data()[0]
    policy = Policy()
    for _ in range(30):
        shown = disclose(sample_full_labels(tax, cid, rng, policy), rng, policy, "vague")
        assert all(v == UNKNOWN for f, v in shown.items() if f not in ("equipment_class", "subunit"))


def test_describe_uses_taxonomy_names(tax):
    rng = random.Random(6)
    cid = tax.class_ids_with_data()[0]
    lab = sample_full_labels(tax, cid, rng)
    facts = describe(tax, lab)
    assert facts["equipment_class"]["name"] == tax.classes[cid]["name"]
    assert set(facts) == {f for f, v in lab.items() if v != UNKNOWN}
