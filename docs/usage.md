# Using the converter

Input is messy work-order text; output is one record per distinct failure or maintenance event, with every field either a taxonomy value (with a verbatim quote from the text as evidence) or `unknown` plus a warning.

## Setup
```
uv sync
```
You need your own `taxonomy/*.csv` built from your licensed copy of ISO 14224 (see `taxonomy/README.md`).

### Model backends (pick one; no API key is required for the last two)
| Backend | Needs | Notes |
|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` in a git-ignored `.env` (copy `.env.example`) | Best quality and speed; prompt caching. Default models: generator `claude-opus-5-5`, extractor `claude-sonnet-5-5`. |
| `claude-cli` | the Claude Code CLI, logged in | Uses your Claude plan, no key. One process per call (~4 s). Models: `opus`, `sonnet`, `haiku`. |
| `ollama` | a local Ollama server | Free and offline. Pass a model, e.g. `--model qwen3.5:latest`. Small models follow instructions less reliably and are slow (about 25 s per call on the test machine). |

Select with `--backend` or the env vars `W2D_BACKEND` (both roles), `W2D_GENERATOR_BACKEND`, `W2D_EXTRACTOR_BACKEND`; models with `--model` or `W2D_GENERATOR_MODEL` / `W2D_EXTRACTOR_MODEL`. The extractor and the generator of the synthetic test data must be different models; the CLI refuses otherwise. Effort for the extractor: env `W2D_EXTRACTOR_EFFORT` (default `medium`).

## One text
```
uv run w2d convert "P-101A mech seal leaking, found worn faces, replaced seal"
uv run w2d convert "..." --backend ollama --model qwen3.5:latest   # no API key
uv run w2d convert "..." --json
uv run w2d convert "..." --dry-run        # token estimate, no API call
```

## Batch
```
# CSV: choose the columns (several text columns are combined, each labelled with its column name)
uv run w2d batch export.csv --id-col wo_number --text-col description,comments \
    --tag-col functional_location --date-col created --out-dir data/out

# one text file (many orders split on a line containing only ---), or a folder of .txt files
uv run w2d batch orders.txt
uv run w2d batch notes_folder/

uv run w2d batch export.csv ... --dry-run   # estimate only
uv run w2d batch export.csv ... --resume    # skip orders already in records.jsonl
```
Outputs in `--out-dir`:
- `records.jsonl` - master output, one record per event (all fields, evidence, warnings).
- `records.csv` - flat view: per field a code column (blank = unknown, fill it in) and a `_name` column, plus `needs_review` and `warnings`.
- `predictions.jsonl` - the same labels in the format `w2d eval` reads.
The command ends with a summary: records, how many need review, which fields are unknown, warning counts.

## Optional inputs
- `--glossary configs/glossary.example.csv` - site abbreviations (`abbreviation,meaning`). Shown to the model as hints; the text is never rewritten, so quotes stay verbatim. The example is a starter list for you to correct.
- `--tag-map configs/tag_prefix_map.example.yaml` - tag prefix to class id. A mapped prefix beats the model's class guess (warning if they disagree). Tags are found in the text with a configurable regex; a tag column is used as given.
- Tags, dates and other CSV columns are passed through; dates and man-hours are not yet parsed from free text.

## How it works
1. **Stage 1:** the model splits the text into events and picks each event's equipment class from the class list.
2. **Stage 2:** for each event the model sees only that class's subunits, items and failure modes (plus the class-independent mechanism, cause, detection and activity lists) and fills the fields with evidence quotes.
3. **Validation (code, no model):** a value must be in the allowed list and its evidence must be found in the text (case and whitespace tolerant; the stored quote is the original span). Otherwise: one retry, then `unknown` plus a warning. An item implies its subunit; an item in a different subunit is dropped.
4. **No-data classes:** if the class is known but the taxonomy has no subunit/failure-mode data for it, the class is kept, those fields are `unknown`, and the record is flagged.

## Warnings
| Reason | Meaning |
|---|---|
| `not_stated` | the text does not state this field (fill it in if you know it) |
| `class_unknown` | equipment could not be identified |
| `no_taxonomy_data` | known class, but no subunit/failure-mode data in the taxonomy |
| `not_in_taxonomy` | the model proposed a value that is not allowed (dropped) |
| `evidence_not_found` | the model's quote is not in the text (dropped) |
| `class_conflicts_with_tag` | tag prefix maps to a different class than the text suggests (tag wins) |
| `conflicting` | fields disagree (e.g. item not in subunit) |
| `model_error` | the model gave no usable answer |
| `unverified_taxonomy` | some matched taxonomy rows are not yet verified by a domain expert |

`needs_review` is set when the class is unknown, when there is any warning other than `not_stated`/`unverified_taxonomy`, or when neither a failure mode nor a maintenance activity was found.

## Evaluate against synthetic gold
```
uv run w2d predict --split test              # run the extractor on synthetic samples -> data/synthetic/preds.jsonl
uv run w2d eval --pred data/synthetic/preds.jsonl --split test --reviewed-only
```
See `docs/synthetic-data.md`.

## Cost
Each work order costs two model calls (stage 1, stage 2) plus one more per extra event. The taxonomy lists are sent as a cacheable prefix (about 2-4k tokens per class), so cost per order falls after the first order of each class. `--dry-run` shows an estimate before caching.
