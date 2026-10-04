# Synthetic test data and evaluation

Real work orders are not available yet, so the first test set is synthetic and **label-first**: the labels come from the taxonomy, and a *different* model writes the messy text. The generated files contain ISO codes and names, so they live in `data/` (git-ignored) and are never committed.

## How a sample is made
1. **Sample** a taxonomy-valid label set for one event: class → subunit → maintainable item → failure mode (valid for that class) → mechanism, cause, detection method, maintenance category and activity. Generic catch-all entries ("other", "unknown", "general", "no cause found") are skipped.
2. **Plausibility check:** a model rejects physically implausible combinations (the standard does not say which mechanisms fit which modes). Rejected combinations are resampled.
3. **Disclosure:** each field is revealed in the text with a set probability; hidden fields get the gold label `unknown`. This is what tests the never-guess rule. The equipment class is always named, except in deliberately vague entries.
4. **Writing:** a model writes the text in one of five styles (terse shorthand, narrative, typos, report form, mixed), revealing only the disclosed facts, and returns the phrase that states each one. Phrases not found verbatim in the text are dropped.
5. **Kinds:** about 80% single-event, 10% multi-event (two issues on one order, two gold records), 10% vague.

## Size and splits
200 samples over the classes that have both subunits and failure modes: at least 3 per class, weighted toward pumps, compressors, electric motors, valves and heat exchangers (`configs/synth_weights.json`). 60 are the **test** split (stratified over classes), 140 are **dev**. Tune prompts on dev only; keep test frozen.

## Workflow
```
uv run w2d synth generate --dry-run   # allocation and estimated usage, no API calls
uv run w2d synth generate             # anthropic backend (needs ANTHROPIC_API_KEY in .env); writes data/synthetic/samples.jsonl
uv run w2d synth generate --backend claude-cli    # no API key: uses your Claude Code login
# small trial: --total 27 --floor 1 --test 9
uv run w2d synth review-export        # data/synthetic/review.csv (test split)
# review: fix wrong cells (replace with the right code, or `unknown`), put y in the ok column
uv run w2d synth review-import        # applies ok rows, marks fully reviewed samples
uv run w2d eval --pred preds.jsonl --split test --reviewed-only
```
The generator model (anthropic default `claude-opus-5-5`, claude-cli `opus`; env `W2D_GENERATOR_MODEL`) must differ from the extractor model (anthropic default `claude-sonnet-5-5`, claude-cli `sonnet`, ollama: you choose; env `W2D_EXTRACTOR_MODEL`); the CLI refuses otherwise, and `eval` warns if a prediction file was produced by the generator model.

## Review CSV
One row per gold record. Cells read `<code> | <name>` or `unknown`. To correct, replace the cell with the correct code; text after ` | ` is ignored. Only rows with `ok` = `y` are applied. Invalid codes abort the import with a list of every bad cell and change nothing. A sample counts as *reviewed* (gold) once every one of its records is ok; the rest stay silver.

## Metrics
Per field, for each aligned gold/predicted record pair:

| Outcome | Meaning | Counts against |
|---|---|---|
| correct | predicted = gold, both known | none |
| wrong | both known but different | precision and recall |
| hallucinated | predicted a value where gold is `unknown` | precision |
| missed | predicted `unknown` where gold is known | recall |
| (both unknown) | correct abstention | none |

A wrong value is worse than `unknown`, so precision is reported next to recall. Records of a multi-event order are paired by how many key fields agree (class, subunit, item, failure mode, activity); unpaired gold records count as missed, unpaired predicted records as hallucinated. Per-class numbers are shown only for classes with enough gold values.

## Prediction file format
JSONL, one object per work order: `{"source_id": "...", "records": [{"labels": {"equipment_class": "...", ...}}], "meta": {"model": "..."}}`. Fields use the same ids as the gold labels (class id, subunit id, item id, codes) or `unknown`.

## Caveats
- Synthetic text is more regular than real text; scores are an upper bound until real, anonymised orders are added.
- Gold labels are only as good as the taxonomy rows they use (all `verified=false` until the CMRP verifies them); `meta.taxonomy_all_verified` records this per sample.
