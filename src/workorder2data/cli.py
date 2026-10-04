"""w2d command line. Thin wrappers over the library; `convert` arrives with the extractor (Phase 1d)."""

import argparse
import json
import os
import sys
from pathlib import Path

from workorder2data.convert import convert_many, make_extractor, summarize
from workorder2data.evaluate import evaluate, format_report, report_to_dict
from workorder2data.inputs import CsvMapping, WorkOrder, read_csv, read_text_file
from workorder2data.records import Record, to_predictions, write_records_csv, write_records_jsonl
from workorder2data.llm import BACKENDS, LLMError, backend_for, load_dotenv, make_client, resolve_model
from workorder2data.schema import Prediction, Sample, read_jsonl, write_jsonl
from workorder2data.synth.sampler import allocate, split_test_counts
from workorder2data.taxonomy import Taxonomy

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[2] / "configs" / "synth_weights.json"
DEFAULT_SAMPLES = Path("data/synthetic/samples.jsonl")

# rough per-call token sizes used only for the --dry-run estimate
EST_TOKENS = {"plausibility": (450, 80), "writer": (700, 300)}


def _weights(path: Path, class_ids: list[str]) -> dict[str, float]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    common = set(cfg["common_class_ids"])
    return {c: cfg["common_weight"] if c in common else cfg["default_weight"] for c in class_ids}


def cmd_generate(args: argparse.Namespace) -> int:
    from workorder2data.synth.generate import generate

    gen_backend = backend_for("generator", args.backend)
    model = resolve_model("generator", gen_backend, args.model)
    try:
        ext_backend = backend_for("extractor")
        extractor = (ext_backend, resolve_model("extractor", ext_backend))
    except ValueError:
        extractor = None  # extractor not configured yet: nothing to clash with
    if extractor == (gen_backend, model):
        print(f"error: generator and extractor are both {model} on {gen_backend}; they must differ", file=sys.stderr)
        return 2
    tax = Taxonomy()
    class_ids = tax.class_ids_with_data()
    weights = _weights(Path(args.weights), class_ids)
    counts = allocate(class_ids, weights, args.total, args.floor)
    test_counts = split_test_counts(counts, args.test)
    if args.dry_run:
        n = args.total
        calls_p, calls_w = int(n * 1.3), int(n * 1.1)
        tin = calls_p * EST_TOKENS["plausibility"][0] + calls_w * EST_TOKENS["writer"][0]
        tout = calls_p * EST_TOKENS["plausibility"][1] + calls_w * EST_TOKENS["writer"][1]
        print(f"dry run: {n} samples over {len(class_ids)} classes ({sum(test_counts.values())} test / {n - sum(test_counts.values())} dev), model {model}")
        for c in class_ids:
            print(f"  {c:38} {counts[c]:3d} total  {test_counts[c]:2d} test")
        print(f"estimated calls: ~{calls_p} plausibility + ~{calls_w} writer; tokens: ~{tin} in / ~{tout} out")
        print("no API calls were made")
        return 0
    load_dotenv()
    client = make_client(gen_backend, effort="low", base_url=args.base_url)
    samples = generate(tax, client, model=model, total=args.total, test_total=args.test, seed=args.seed,
                       weights=weights, floor=args.floor, progress=lambda m: print(m, file=sys.stderr))
    write_jsonl(Path(args.out), samples)
    print(f"wrote {len(samples)} samples to {args.out} "
          f"({sum(s.split == 'test' for s in samples)} test / {sum(s.split == 'dev' for s in samples)} dev)")
    return 0


def cmd_review_export(args: argparse.Namespace) -> int:
    from workorder2data.synth.review import export_csv

    n = export_csv(read_jsonl(Path(args.samples), Sample), Taxonomy(), Path(args.out), split=args.split)
    print(f"wrote {n} record rows to {args.out}")
    return 0


def cmd_review_import(args: argparse.Namespace) -> int:
    from workorder2data.synth.review import ReviewError, import_csv

    path = Path(args.samples)
    samples = read_jsonl(path, Sample)
    try:
        applied, reviewed = import_csv(samples, Taxonomy(), Path(args.csv))
    except ReviewError as e:
        print("review file has problems; nothing was changed:\n" + str(e), file=sys.stderr)
        return 2
    write_jsonl(path, samples)
    print(f"applied {applied} rows; {reviewed} sample(s) newly marked reviewed; total reviewed: {sum(s.reviewed for s in samples)}")
    return 0


def _extractor(args: argparse.Namespace):
    return make_extractor(
        model=args.model,
        glossary_path=Path(args.glossary) if args.glossary else None,
        tag_map_path=Path(args.tag_map) if args.tag_map else None,
        backend=args.backend,
        base_url=args.base_url,
    )


def _check_models(args: argparse.Namespace) -> str | None:
    """Error text if the extractor is also the synthetic-data generator (they must differ), else None."""
    ext_backend = backend_for("extractor", args.backend)
    ext = (ext_backend, resolve_model("extractor", ext_backend, args.model))
    try:
        gen_backend = backend_for("generator")
        gen = (gen_backend, resolve_model("generator", gen_backend))
    except ValueError:
        return None
    if ext == gen:
        return f"error: extractor {ext[1]} on {ext[0]} is also the synthetic-data generator; they must differ"
    return None


def _print_record(r: Record) -> None:
    print(f"{r.record_id}  tag={r.tag or '-'}  needs_review={'yes' if r.needs_review else 'no'}")
    for f, v in r.fields.items():
        if v.value != "unknown":
            print(f"  {f:22} {v.value}  ({v.name})  <- \"{v.evidence}\"")
    for w in r.warnings:
        if w.reason != "unverified_taxonomy":
            print(f"  ! {w.field or 'record'}: {w.reason}" + (f" - {w.detail}" if w.detail else ""))


def cmd_convert(args: argparse.Namespace) -> int:
    ex = _extractor(args)
    if args.dry_run:
        tin, tout = ex.estimate_tokens([args.text])
        print(f"dry run: ~{tin} input / ~{tout} output tokens before prompt caching, model {ex.model}; no API calls were made")
        return 0
    records = ex.convert(args.text, source_id=args.id)
    if args.json:
        print(json.dumps([r.model_dump() for r in records], ensure_ascii=False, indent=1))
    else:
        for r in records:
            _print_record(r)
    return 0


def _read_workorders(args: argparse.Namespace) -> list[WorkOrder]:
    path = Path(args.input)
    if path.is_dir():
        return [wo for f in sorted(path.glob("*.txt")) for wo in read_text_file(f)]
    if path.suffix.lower() in (".csv", ".tsv"):
        if not args.id_col or not args.text_col:
            raise ValueError("CSV input needs --id-col and --text-col (comma-separated for several columns)")
        mapping = CsvMapping(args.id_col, [c.strip() for c in args.text_col.split(",")], args.tag_col, args.date_col)
        return read_csv(path, mapping, delimiter="\t" if path.suffix.lower() == ".tsv" else args.delimiter)
    return read_text_file(path)


def cmd_batch(args: argparse.Namespace) -> int:
    from workorder2data.schema import read_jsonl

    if (err := _check_models(args)):
        print(err, file=sys.stderr)
        return 2
    try:
        workorders = _read_workorders(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ex = _extractor(args)
    out = Path(args.out_dir)
    jsonl = out / "records.jsonl"
    done: set[str] = set()
    if args.resume and jsonl.exists():
        done = {r.source_id for r in read_jsonl(jsonl, Record)}
        workorders = [wo for wo in workorders if wo.source_id not in done]
    if args.dry_run:
        tin, tout = ex.estimate_tokens([wo.text for wo in workorders])
        print(f"dry run: {len(workorders)} work order(s), ~{tin} input / ~{tout} output tokens before prompt caching, model {ex.model}; no API calls were made")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    if not args.resume and jsonl.exists():
        jsonl.unlink()
    with open(jsonl, "a", encoding="utf-8") as fh:
        def keep(wo: WorkOrder, recs: list[Record]) -> None:
            for r in recs:
                fh.write(r.model_dump_json() + "\n")
            fh.flush()
            print(f"[{wo.source_id}] {len(recs)} record(s)", file=sys.stderr)

        convert_many(ex, workorders, on_records=keep)
    records = read_jsonl(jsonl, Record)
    write_records_csv(out / "records.csv", records)
    from workorder2data.schema import write_jsonl

    write_jsonl(out / "predictions.jsonl", to_predictions(records, model=ex.model))
    print(summarize(records, len({r.source_id for r in records})))
    print(f"wrote {out / 'records.jsonl'}, {out / 'records.csv'}, {out / 'predictions.jsonl'}")
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    if (err := _check_models(args)):
        print(err, file=sys.stderr)
        return 2
    samples = [s for s in read_jsonl(Path(args.samples), Sample) if args.split is None or s.split == args.split]
    ex = _extractor(args)
    if args.dry_run:
        tin, tout = ex.estimate_tokens([s.text for s in samples])
        print(f"dry run: {len(samples)} sample(s), ~{tin} input / ~{tout} output tokens before prompt caching, model {ex.model}; no API calls were made")
        return 0
    workorders = [WorkOrder(source_id=s.source_id, text=s.text) for s in samples]
    records = convert_many(ex, workorders, on_records=lambda wo, r: print(f"[{wo.source_id}] {len(r)} record(s)", file=sys.stderr))
    write_jsonl(Path(args.out), to_predictions(records, model=ex.model))
    print(f"wrote predictions for {len(samples)} sample(s) to {args.out}; score with: w2d eval --pred {args.out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    gold = read_jsonl(Path(args.gold), Sample)
    preds = read_jsonl(Path(args.pred), Prediction)
    report = evaluate(gold, preds, split=args.split, reviewed_only=args.reviewed_only)
    print(format_report(report, min_class_known=args.min_class_known))
    if args.json:
        Path(args.json).write_text(json.dumps(report_to_dict(report), indent=1), encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="w2d", description="Convert CMMS maintenance text into ISO 14224 records; synthetic test data and evaluation tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    synth = sub.add_parser("synth", help="synthetic test data").add_subparsers(dest="synth_cmd", required=True)
    g = synth.add_parser("generate", help="generate synthetic labeled work orders (needs an API key unless --dry-run)")
    g.add_argument("--out", default=str(DEFAULT_SAMPLES))
    g.add_argument("--total", type=int, default=200)
    g.add_argument("--test", type=int, default=60, help="how many samples go to the test split")
    g.add_argument("--floor", type=int, default=3, help="minimum samples per class")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--model", help="writer model (backend default: anthropic claude-opus-5-5, claude-cli opus); must differ from the extractor")
    g.add_argument("--backend", choices=BACKENDS, help="model backend (default env W2D_GENERATOR_BACKEND / W2D_BACKEND / anthropic)")
    g.add_argument("--base-url", help="server URL for the ollama backend (default http://localhost:11434)")
    g.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    g.add_argument("--dry-run", action="store_true", help="show allocation and estimated usage; no API calls")
    g.set_defaults(func=cmd_generate)

    e = synth.add_parser("review-export", help="write a CSV for the CMRP to review gold labels")
    e.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    e.add_argument("--out", default="data/synthetic/review.csv")
    e.add_argument("--split", choices=["dev", "test"], default="test")
    e.set_defaults(func=cmd_review_export)

    i = synth.add_parser("review-import", help="apply a reviewed CSV (rows marked ok=y) back to the samples")
    i.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    i.add_argument("--csv", default="data/synthetic/review.csv")
    i.set_defaults(func=cmd_review_import)

    def model_opts(sp):
        sp.add_argument("--model", help="extractor model (backend default: anthropic claude-sonnet-5-5, claude-cli sonnet; ollama needs one, e.g. qwen3.5:latest)")
        sp.add_argument("--backend", choices=BACKENDS, help="model backend (default env W2D_EXTRACTOR_BACKEND / W2D_BACKEND / anthropic)")
        sp.add_argument("--base-url", help="server URL for the ollama backend (default http://localhost:11434)")
        sp.add_argument("--glossary", help="CSV of abbreviation,meaning (see configs/glossary.example.csv)")
        sp.add_argument("--tag-map", help="YAML mapping equipment-tag prefixes to class ids")
        sp.add_argument("--dry-run", action="store_true", help="estimate tokens only; no API calls")

    c = sub.add_parser("convert", help="convert one work-order text")
    c.add_argument("text")
    c.add_argument("--id", default="wo")
    c.add_argument("--json", action="store_true")
    model_opts(c)
    c.set_defaults(func=cmd_convert)

    b = sub.add_parser("batch", help="convert a CSV, a text file (split on '---') or a folder of .txt files")
    b.add_argument("input")
    b.add_argument("--out-dir", default="data/out")
    b.add_argument("--id-col")
    b.add_argument("--text-col", help="column(s) holding the text, comma-separated")
    b.add_argument("--tag-col")
    b.add_argument("--date-col")
    b.add_argument("--delimiter", default=",")
    b.add_argument("--resume", action="store_true", help="skip work orders already in out-dir/records.jsonl")
    model_opts(b)
    b.set_defaults(func=cmd_batch)

    pr = sub.add_parser("predict", help="run the extractor on synthetic samples and write predictions for `eval`")
    pr.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    pr.add_argument("--split", choices=["dev", "test"])
    pr.add_argument("--out", default="data/synthetic/preds.jsonl")
    model_opts(pr)
    pr.set_defaults(func=cmd_predict)

    ev = sub.add_parser("eval", help="score predictions against gold labels")
    ev.add_argument("--gold", default=str(DEFAULT_SAMPLES))
    ev.add_argument("--pred", required=True)
    ev.add_argument("--split", choices=["dev", "test"])
    ev.add_argument("--reviewed-only", action="store_true", help="score only samples a human has reviewed (gold)")
    ev.add_argument("--min-class-known", type=int, default=10)
    ev.add_argument("--json", help="also write the report as JSON")
    ev.set_defaults(func=cmd_eval)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, LLMError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
