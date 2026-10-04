"""Structural cross-checks on the taxonomy tables.

Run against the user's real taxonomy/*.csv when present, otherwise against the
fictional example in tests/fixtures/taxonomy. They catch transcription slips that
can be found mechanically; they do NOT prove values match the standard (that is
the user's verification step). ISO-specific checks live in test_taxonomy_iso.py.
"""

import pytest

from workorder2data.taxonomy import load_table

COMMON = ["source_table", "source_page", "verified"]
COLUMNS = {
    "equipment_classes": ["class_id", "code", "name", "category", "category_ref", "annex_a_ref", "footnote_refs"],
    "equipment_categories": ["category", "annex_ref", "failure_mode_table", "footnote_refs"],
    "failure_mechanisms": ["code", "name", "level", "parent_code", "description", "footnote_refs"],
    "failure_causes": ["code", "name", "level", "parent_code", "description", "footnote_refs"],
    "detection_methods": ["code", "name", "activity_group", "description", "footnote_refs"],
    "maintenance_activities": ["code", "name", "description", "examples", "use", "footnote_refs"],
    "footnotes": ["letter", "text"],
}


@pytest.fixture(scope="module")
def tables(taxonomy_dir):
    return {name: load_table(name, taxonomy_dir) for name in [*COLUMNS]}


@pytest.mark.parametrize("name", COLUMNS)
def test_columns_and_provenance(tables, name):
    rows = tables[name]
    assert rows
    for col in [*COLUMNS[name], *COMMON]:
        assert col in rows[0], f"{name}.csv missing column {col}"
    for row in rows:
        assert row["source_page"] > 0
        assert isinstance(row["verified"], bool)


def test_class_ids_unique_and_codes_may_repeat(tables):
    ids = [r["class_id"] for r in tables["equipment_classes"]]
    assert len(ids) == len(set(ids))
    # W1/W2/W3/WC repeat across coiled tubing, snubbing and wireline in the standard,
    # so (code) alone is not a key; (category, name) is.
    keys = {(r["category"], r["name"]) for r in tables["equipment_classes"]}
    assert len(keys) == len(tables["equipment_classes"])


def test_class_categories_exist_and_refs_match(tables):
    cats = {r["category"]: r for r in tables["equipment_categories"]}
    for r in tables["equipment_classes"]:
        assert r["category"] in cats
        assert r["category_ref"] == cats[r["category"]]["annex_ref"]


@pytest.mark.parametrize("name", ["failure_mechanisms", "failure_causes"])
def test_hierarchy(tables, name):
    rows = tables[name]
    codes = {r["code"] for r in rows}
    assert len(codes) == len(rows)
    for r in rows:
        if r["level"] == "1":
            assert r["parent_code"] == "" and "." not in r["code"]
        else:
            assert r["level"] == "2"
            assert r["parent_code"] in codes
            assert r["code"].split(".")[0] == r["parent_code"]
    # every level-1 group has a "General" (.0) subdivision
    for r in rows:
        if r["level"] == "1":
            assert f'{r["code"]}.0' in codes


def test_activity_use_values(tables):
    for r in tables["maintenance_activities"]:
        assert set(r["use"].split(";")) <= {"C", "P"}


def test_footnote_refs_resolve(tables):
    known = {(r["source_table"], r["letter"]) for r in tables["footnotes"]}
    assert len(known) == len(tables["footnotes"])
    for name in COLUMNS:
        if name == "footnotes":
            continue
        for r in tables[name]:
            for letter in filter(None, r["footnote_refs"].split(";")):
                assert (r["source_table"], letter) in known, f"{name}: {r['source_table']} {letter}"
    for r in tables["equipment_categories"]:
        for letter in filter(None, r["footnote_refs"].split(";")):
            assert (r["source_table"], letter) in known
