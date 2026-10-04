"""Score predicted records against gold labels, per field.

For each field and each aligned record pair:
  correct       predicted value == gold value (both known)
  wrong         both known, values differ        -> counts against precision AND recall
  hallucinated  predicted a value, gold is unknown -> counts against precision
  missed        predicted unknown, gold is known   -> counts against recall
  (both unknown is a correct abstention and is not counted)
A wrong value is worse than `unknown`, so precision is reported on its own next to recall.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from workorder2data.schema import LABEL_FIELDS, UNKNOWN, LabeledRecord, Prediction, Sample

ALIGN_FIELDS = ("equipment_class", "subunit", "maintainable_item", "failure_mode", "maintenance_activity")


@dataclass
class Counts:
    correct: int = 0
    wrong: int = 0
    hallucinated: int = 0
    missed: int = 0

    def add(self, gold: str, pred: str) -> None:
        if pred != UNKNOWN and gold != UNKNOWN:
            if pred == gold:
                self.correct += 1
            else:
                self.wrong += 1
        elif pred != UNKNOWN:
            self.hallucinated += 1
        elif gold != UNKNOWN:
            self.missed += 1

    def merge(self, other: "Counts") -> None:
        self.correct += other.correct
        self.wrong += other.wrong
        self.hallucinated += other.hallucinated
        self.missed += other.missed

    @property
    def precision(self) -> float | None:
        d = self.correct + self.wrong + self.hallucinated
        return self.correct / d if d else None

    @property
    def recall(self) -> float | None:
        d = self.correct + self.wrong + self.missed
        return self.correct / d if d else None

    @property
    def gold_known(self) -> int:
        return self.correct + self.wrong + self.missed


@dataclass
class Report:
    n_samples: int = 0
    n_gold_records: int = 0
    n_pred_records: int = 0
    n_unmatched_gold: int = 0
    n_unmatched_pred: int = 0
    fields: dict[str, Counts] = field(default_factory=lambda: {f: Counts() for f in LABEL_FIELDS})
    by_class: dict[str, dict[str, Counts]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def overall(self) -> Counts:
        total = Counts()
        for c in self.fields.values():
            total.merge(c)
        return total


def align(gold: list[LabeledRecord], pred: list[LabeledRecord]) -> list[tuple[int | None, int | None]]:
    """Greedy pairing of gold and predicted records by how many key fields agree; leftovers stay unmatched."""
    scored = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            s = sum(1 for f in ALIGN_FIELDS if g.value(f) != UNKNOWN and g.value(f) == p.value(f))
            scored.append((s, gi, pi))
    pairs, used_g, used_p = [], set(), set()
    for s, gi, pi in sorted(scored, key=lambda t: (-t[0], t[1], t[2])):
        if gi in used_g or pi in used_p:
            continue
        if s == 0 and (len(gold) > 1 or len(pred) > 1):
            continue  # only force a zero-overlap pairing in the 1-to-1 case
        pairs.append((gi, pi))
        used_g.add(gi)
        used_p.add(pi)
    pairs += [(gi, None) for gi in range(len(gold)) if gi not in used_g]
    pairs += [(None, pi) for pi in range(len(pred)) if pi not in used_p]
    return pairs


def evaluate(
    gold: list[Sample],
    preds: list[Prediction],
    *,
    split: str | None = None,
    reviewed_only: bool = False,
) -> Report:
    by_id = {p.source_id: p for p in preds}
    report = Report()
    selected = [s for s in gold if (split is None or s.split == split) and (s.reviewed or not reviewed_only)]
    report.n_samples = len(selected)
    gen_models = {s.meta.get("generator_model") for s in selected} - {None}
    pred_models = {p.meta.get("model") for p in preds} - {None}
    if gen_models & pred_models:
        report.warnings.append(
            f"extractor model {sorted(gen_models & pred_models)} also generated the test text; scores are optimistic"
        )
    missing = [s.source_id for s in selected if s.source_id not in by_id]
    if missing:
        report.warnings.append(f"{len(missing)} sample(s) have no prediction (scored as all missed)")
    for s in selected:
        p = by_id.get(s.source_id)
        pred_records = p.records if p else []
        report.n_gold_records += len(s.records)
        report.n_pred_records += len(pred_records)
        cls = s.meta.get("true_class") or s.records[0].value("equipment_class")
        cls_counts = report.by_class.setdefault(cls, {f: Counts() for f in LABEL_FIELDS})
        for gi, pi in align(s.records, pred_records):
            if gi is None:
                report.n_unmatched_pred += 1
            if pi is None:
                report.n_unmatched_gold += 1
            for f in LABEL_FIELDS:
                g = s.records[gi].value(f) if gi is not None else UNKNOWN
                q = pred_records[pi].value(f) if pi is not None else UNKNOWN
                report.fields[f].add(g, q)
                cls_counts[f].add(g, q)
    return report


def _pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{100 * x:5.1f}"


def format_report(report: Report, min_class_known: int = 10) -> str:
    lines = [
        f"samples: {report.n_samples}   gold records: {report.n_gold_records}   predicted records: {report.n_pred_records}"
        f"   unmatched gold/pred: {report.n_unmatched_gold}/{report.n_unmatched_pred}",
        "",
        f"{'field':22} {'prec%':>6} {'rec%':>6} {'correct':>8} {'wrong':>6} {'halluc':>7} {'missed':>7}",
    ]
    for f in LABEL_FIELDS:
        c = report.fields[f]
        lines.append(f"{f:22} {_pct(c.precision):>6} {_pct(c.recall):>6} {c.correct:8d} {c.wrong:6d} {c.hallucinated:7d} {c.missed:7d}")
    o = report.overall()
    lines.append(f"{'ALL FIELDS':22} {_pct(o.precision):>6} {_pct(o.recall):>6} {o.correct:8d} {o.wrong:6d} {o.hallucinated:7d} {o.missed:7d}")
    rows = []
    for cls, fields in sorted(report.by_class.items()):
        tot = Counts()
        for c in fields.values():
            tot.merge(c)
        if tot.gold_known >= min_class_known:
            rows.append(f"{cls:38} {_pct(tot.precision):>6} {_pct(tot.recall):>6} {tot.gold_known:8d}")
    if rows:
        lines += ["", f"per class (classes with >= {min_class_known} gold values)", f"{'class':38} {'prec%':>6} {'rec%':>6} {'gold':>8}"] + rows
    lines += [f"WARNING: {w}" for w in report.warnings]
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    def pack(c: Counts) -> dict:
        return {"correct": c.correct, "wrong": c.wrong, "hallucinated": c.hallucinated, "missed": c.missed,
                "precision": c.precision, "recall": c.recall}

    out = defaultdict(dict)
    out["samples"] = report.n_samples
    out["fields"] = {f: pack(c) for f, c in report.fields.items()}
    out["overall"] = pack(report.overall())
    out["by_class"] = {cls: {f: pack(c) for f, c in fs.items()} for cls, fs in report.by_class.items()}
    out["warnings"] = report.warnings
    return dict(out)
