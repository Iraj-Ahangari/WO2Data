"""Checks that only make sense on the real ISO 14224:2016 transcription.

Skipped in a clone without taxonomy/*.csv (the ISO-derived tables are not distributed
with this repo). Counts are as transcribed from the PDF page images; a change means
the data changed.
"""

import pytest

from conftest import has_real_taxonomy
from workorder2data.taxonomy import load_table

pytestmark = pytest.mark.skipif(not has_real_taxonomy(), reason="real ISO taxonomy CSVs not present")

EXPECTED_COUNTS = {
    "equipment_classes": 108,
    "equipment_categories": 11,
    "failure_mechanisms": 44,
    "failure_causes": 26,
    "detection_methods": 11,
    "maintenance_activities": 12,
}


@pytest.mark.parametrize("name,count", EXPECTED_COUNTS.items())
def test_row_counts(name, count):
    assert len(load_table(name)) == count


def test_failure_mode_tables_are_b6_to_b14():
    cats = load_table("equipment_categories")
    used = [r["failure_mode_table"] for r in cats]
    assert set(used) <= {f"B.{n}" for n in range(6, 15)} | {""}
    # each of B.6..B.14 belongs to exactly one category
    assert sorted(t for t in used if t) == sorted(f"B.{n}" for n in range(6, 15))


def test_detection_and_activity_codes_contiguous():
    assert [int(r["code"]) for r in load_table("detection_methods")] == list(range(1, 12))
    assert [int(r["code"]) for r in load_table("maintenance_activities")] == list(range(1, 13))
