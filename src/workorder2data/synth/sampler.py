"""Label-first sampling: draw valid taxonomy combinations, then decide how much of each the text will reveal.

All values come from the taxonomy tables. The few policy constants below are sampling choices
(not taxonomy values): which generic entries to skip, and the probabilities used.
"""

import random
from dataclasses import dataclass, field

from workorder2data.schema import LABEL_FIELDS, MAINTENANCE_CATEGORIES, UNKNOWN
from workorder2data.taxonomy import Taxonomy

FAILURE_FIELDS = ("failure_mode", "failure_mechanism", "failure_cause", "detection_method")
CATEGORY_LETTER = {"corrective": "C", "preventive": "P"}


@dataclass(frozen=True)
class Policy:
    # Generic catch-all entries carry no information; skip them as sampled gold labels.
    skip_names: frozenset = frozenset({"other", "unknown", "no cause found"})
    skip_substrings: tuple = ("general",)
    p_corrective: float = 0.7
    p_failure_found_in_pm: float = 0.4
    kinds: tuple = (("single", 0.8), ("multi", 0.1), ("vague", 0.1))
    disclosure: dict = field(
        default_factory=lambda: {
            "equipment_class": 1.0,  # real work orders name the equipment
            "subunit": 0.85,
            "maintainable_item": 0.6,
            "failure_mode": 0.9,
            "failure_mechanism": 0.4,
            "failure_cause": 0.25,
            "detection_method": 0.5,
            "maintenance_category": 0.7,
            "maintenance_activity": 0.9,
        }
    )
    vague_disclosure: dict = field(default_factory=lambda: {"equipment_class": 0.7, "subunit": 0.3})
    min_disclosed: int = 2
    priority: tuple = ("equipment_class", "failure_mode", "maintenance_activity")


def _usable(row: dict, policy: Policy) -> bool:
    name = row["name"].strip().lower()
    return name not in policy.skip_names and not any(s in name for s in policy.skip_substrings)


def allocate(class_ids: list[str], weights: dict[str, float], total: int, floor: int) -> dict[str, int]:
    """Counts per class: `floor` each, the rest spread by weight (largest remainder)."""
    counts = {c: floor for c in class_ids}
    rest = total - floor * len(class_ids)
    if rest < 0:
        raise ValueError(f"total {total} is below floor*classes = {floor * len(class_ids)}")
    wsum = sum(weights[c] for c in class_ids)
    shares = {c: rest * weights[c] / wsum for c in class_ids}
    for c in class_ids:
        counts[c] += int(shares[c])
    leftover = total - sum(counts.values())
    for c in sorted(class_ids, key=lambda c: shares[c] - int(shares[c]), reverse=True)[:leftover]:
        counts[c] += 1
    return counts


def split_test_counts(counts: dict[str, int], test_total: int) -> dict[str, int]:
    """How many of each class's records go to the test split (>=1 test and >=1 dev when a class has 2+)."""
    n = sum(counts.values())
    shares = {c: test_total * k / n for c, k in counts.items()}
    test = {c: (max(1, int(shares[c])) if k >= 2 else 0) for c, k in counts.items()}
    while sum(test.values()) < test_total:
        cands = [c for c in counts if test[c] < counts[c] - 1]
        test[max(cands, key=lambda c: shares[c] - test[c])] += 1
    while sum(test.values()) > test_total:
        cands = [c for c in counts if test[c] > (1 if counts[c] >= 2 else 0)]
        test[min(cands, key=lambda c: shares[c] - test[c])] -= 1
    return test


def sample_full_labels(
    tax: Taxonomy, class_id: str, rng: random.Random, policy: Policy = Policy(), avoid_subunit: str | None = None
) -> dict[str, str]:
    """A fully specified, taxonomy-valid label set for one event (unlabeled failure fields = unknown)."""
    category = "corrective" if rng.random() < policy.p_corrective else "preventive"
    has_failure = category == "corrective" or rng.random() < policy.p_failure_found_in_pm
    subunits = tax.subunits_by_class[class_id]
    choices = [s for s in subunits if s["subunit_id"] != avoid_subunit] or subunits
    subunit = rng.choice(choices)
    item = rng.choice(tax.items_by_subunit[subunit["subunit_id"]])
    letter = CATEGORY_LETTER[category]
    activities = [a for a in tax.activities.values() if letter in a["use"].split(";") and _usable(a, policy)]
    labels = {
        "equipment_class": class_id,
        "subunit": subunit["subunit_id"],
        "maintainable_item": item["item_id"],
        "maintenance_category": category,
        "maintenance_activity": rng.choice(activities)["code"],
    }
    if has_failure:
        labels["failure_mode"] = rng.choice(tax.failure_modes_by_class[class_id])["code"]
        labels["failure_mechanism"] = rng.choice(
            [m for m in tax.mechanisms.values() if m["level"] == "2" and _usable(m, policy)]
        )["code"]
        labels["failure_cause"] = rng.choice(
            [c for c in tax.causes.values() if c["level"] == "2" and _usable(c, policy)]
        )["code"]
        labels["detection_method"] = rng.choice(
            [d for d in tax.detection_methods.values() if _usable(d, policy)]
        )["code"]
    for f in LABEL_FIELDS:
        labels.setdefault(f, UNKNOWN)
    return labels


def pick_kind(rng: random.Random, policy: Policy = Policy()) -> str:
    kinds, weights = zip(*policy.kinds)
    return rng.choices(kinds, weights)[0]


def disclose(full: dict[str, str], rng: random.Random, policy: Policy, kind: str) -> dict[str, str]:
    """Hide fields the text will not state; hidden fields become `unknown` in the gold label."""
    probs = policy.vague_disclosure if kind == "vague" else policy.disclosure
    shown = {f for f in LABEL_FIELDS if full[f] != UNKNOWN and rng.random() < probs.get(f, 0.0)}
    if kind != "vague":
        for f in policy.priority:
            if len(shown) >= policy.min_disclosed:
                break
            if full[f] != UNKNOWN:
                shown.add(f)
    elif not shown and full["equipment_class"] != UNKNOWN:
        shown.add("equipment_class")
    if "maintainable_item" in shown:
        shown.add("subunit")  # an item id names its subunit, so the subunit cannot stay hidden
    return {f: (full[f] if f in shown else UNKNOWN) for f in LABEL_FIELDS}


def describe(tax: Taxonomy, labels: dict[str, str]) -> dict[str, dict]:
    """Human-readable facts for the disclosed fields (names and descriptions straight from the taxonomy)."""
    out: dict[str, dict] = {}
    cid = labels["equipment_class"]
    for f, v in labels.items():
        if v == UNKNOWN:
            continue
        if f == "equipment_class":
            out[f] = {"name": tax.classes[v]["name"]}
        elif f == "subunit":
            out[f] = {"name": tax.subunits[v]["name"]}
        elif f == "maintainable_item":
            out[f] = {"name": tax.items[v]["name"]}
        elif f == "failure_mode":
            fm = next(r for r in tax.failure_modes_by_class[cid] if r["code"] == v)
            out[f] = {"name": fm["name"], "description": fm["examples"]}
        elif f == "failure_mechanism":
            out[f] = {"name": tax.mechanisms[v]["name"], "description": tax.mechanisms[v]["description"]}
        elif f == "failure_cause":
            out[f] = {"name": tax.causes[v]["name"], "description": tax.causes[v]["description"]}
        elif f == "detection_method":
            out[f] = {"name": tax.detection_methods[v]["name"], "description": tax.detection_methods[v]["description"]}
        elif f == "maintenance_activity":
            out[f] = {"name": tax.activities[v]["name"], "description": tax.activities[v]["description"]}
        elif f == "maintenance_category":
            assert v in MAINTENANCE_CATEGORIES
            out[f] = {"name": v}
    return out
