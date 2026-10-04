# CLAUDE.md

Workorder2data converts raw CMMS text into ISO 14224:2016 structured records. Scope and principles are in `IDEA.md`; schema in `docs/schema.md`; phases in `docs/plan.md`. This is an ETL layer only — no analysis or dashboards.

## Commands
- `uv sync` — install dependencies (Python 3.13, pinned in `.python-version`)
- `uv run pytest` — run tests
- `uv run pytest tests/test_x.py::test_name` — run one test
- `uv run w2d synth generate --dry-run` — synthetic data allocation and usage estimate (no API calls); see `docs/synthetic-data.md`
- `uv run w2d eval --pred preds.jsonl --split test` — score predictions against gold

## Taxonomy rules (most important)
- ISO values (equipment classes, subunits, failure modes, mechanisms, causes, detection methods, maintenance activities) come only from `Resources/ISO-14224.pdf` via `taxonomy/*.csv`. Never write them from memory, never hardcode them in code, prompts, or tests.
- The PDF's text layer is garbled (ligatures, digits, brackets). Transcribe from rendered page images, not from `pdftotext` output.
- Every taxonomy row has `source_table`, `source_page`, and `verified`. New rows are `verified=false`; only the user (CMRP) sets `verified=true`. Never flip it yourself.
- Code loads enums from the CSVs at runtime. Adding an equipment class is a data change, not a code change.
- If a taxonomy fact is missing or unclear, say so and ask the user. Do not fill the gap.

## Output rules
- Every field is a valid taxonomy code or `unknown`. Never guess.
- Every non-unknown value carries a verbatim `evidence` quote from the source text.
- Unknowns, conflicts, invalid values, and unverified-taxonomy usage produce entries in `warnings[]`; `needs_review` is set accordingly.
- One record per distinct failure or maintenance action, linked by `source_id`.
- Model output is validated against the taxonomy. An invalid value is retried once, then becomes `unknown`.
- A mapped tag prefix (`tag_prefix_map.yaml`) overrides the model's equipment-class guess; warn on contradiction.

## Architecture conventions
- One library function, `convert(text) -> list[Record]`. The batch and single-text CLIs are thin wrappers over it.
- Two-stage extraction: stage 1 picks the equipment class; stage 2 sees only that class's allowed values.
- Models sit behind an `Extractor` interface. v1 has an Anthropic adapter only; the model name comes from config or env, never from code. An OpenAI-compatible adapter comes later.
- Schema: Pydantic v2. Deterministic steps (parsing, tags, dates, glossary expansion, validation) never call a model.

## Data and secrets
- Never commit real work-order text, `.env`, API keys, the ISO PDF, or ISO-derived taxonomy tables. `data/`, `.env*`, `Resources/*.pdf`, and `taxonomy/*.csv` are git-ignored.
- Only synthetic examples go in the repo, tagged `synthetic`.
- This repo is open source (MIT) and will be public. ISO 14224 is copyrighted and MIT cannot cover it: the PDF (stamped with a third-party licence) and everything transcribed from it (`taxonomy/*.csv`, including descriptions and footnotes) stay on the user's disk and out of git, docs, tests, prompts, and commit messages.
- Do not paste ISO table content into tracked files (docs, tests, code comments, issues). Tests use the fictional example in `tests/fixtures/taxonomy/`; checks that need the real data skip when `taxonomy/*.csv` is absent.
- Code and docs may reference table numbers and field names ("Table B.5", "failure mechanism") but not reproduce the tables.

## Testing and evaluation
- Prefer test-first (red-green-refactor) for parsing, validation, and schema code.
- Test data is synthetic and label-first: sample valid taxonomy combinations, then a *different* model writes the messy text. The generator must not be the extractor.
- Keep the test split frozen; tune prompts only on the dev split.
- Report per-field precision and recall separately. A wrong value is worse than `unknown`. Report verified and unverified taxonomy coverage separately.
- `--dry-run` on any model-calling command must print an estimated token count and cost without calling the API.

## Tooling and research
- Library API facts (Pydantic, pytest, Anthropic SDK, uv): check Context7 if it is available.
- ISO facts: only `Resources/` and `taxonomy/`. Never send work-order text or ISO content to Context7 or any external search.

## Working style
- The user is a CMRP. Their domain judgment on taxonomy and labels is final; ask rather than assume.
- Ask before generating code where their input matters most (fields, equipment scope, label conventions).
- Match the surrounding code's style; keep comments sparse and explain *why*, not *what*.
