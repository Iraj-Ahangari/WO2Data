# Phased plan

Decisions behind this plan are recorded in `IDEA.md` and `CLAUDE.md`. The taxonomy is data; each phase can be verified on its own.

## Phase 0 — docs and scaffold (done)
`IDEA.md`, `CLAUDE.md`, `docs/`, `git init`, `uv` project (Python 3.13, Pydantic v2, pytest), `.gitignore`, empty `taxonomy/`, `data/`, `tests/`.

## Phase 1a — cross-class taxonomy
- Render the relevant PDF pages to images and transcribe into `taxonomy/`: Tables B.2–B.5 and the A.4 class list. Every row `verified=false` with `source_page`.
- Cross-check script and tests.
- **User:** verify rows (spot-check 10 random rows per file against the PDF, then confirm the rest).

## Phase 1b — topside taxonomy and core code (test-first)
- Failure modes B.6–B.9 and the B.15 class mapping; subunits and maintainable items for the topside classes (rotating, mechanical, electrical, safety and control).
- Taxonomy loader, Pydantic `Record` schema, validators, warning logic.
- **User:** verify class by class, starting with the classes seen most in the plant.

## Phase 1c — synthetic data and eval harness (code done; generation and gold review pending)
- Label-first generator using a different model from the extractor.
- 200 records over the 27 classes that have data: floor of 3 per class, weighted toward common classes; about 20% vague or multi-event; English only; tagged `synthetic`; stored in git-ignored `data/`. Details in `docs/synthetic-data.md`.
- Dev/test split, frozen test split, per-field precision/recall report.
- **User:** review about 60 gold test records, stratified across categories.

## Phase 1d — extractor, library, CLI (code done; needs an API key and a first real run)
- `Extractor` interface plus Anthropic two-stage adapter (stage 1 class, stage 2 class-scoped fields), prompt caching for static taxonomy blocks.
- Deterministic layer: CSV/text parsing with column mapping, tag and date extraction, abbreviation glossary, `tag_prefix_map.yaml`.
- `convert(text) -> list[Record]`; CLI: batch (CSV/text) and single-text; `--dry-run` for cost; JSONL master output plus flat CSV.
- Run eval on the dev split, then once on the frozen test split.

## Phase 2 — remaining categories
Subsea, well completion, drilling, well intervention, marine (B.10–B.14 and their Annex A tables). Data only; rerun eval.

## Later
OpenAI-compatible adapter (local or cloud); real, anonymized work orders to replace the synthetic test set.

## Housekeeping
- After leaving plan mode, install Context7 at user scope: `claude mcp add --scope user --transport http context7 https://mcp.context7.com/mcp`.
- Open source: MIT `LICENSE` (copyright holder to be supplied by the user), public GitHub repo. ISO PDF and `taxonomy/*.csv` are git-ignored; a fresh clone runs on the fictional fixture in `tests/fixtures/taxonomy/`. Before the first push, check `git log --stat` shows no ISO-derived content.
- `Resources/ISO-14224.pdf` has identical copies in `Projects/Standards/` and `Projects/CodeGate/In-Documents/`; keep only the one in `Resources/` as the reference for this repo.
