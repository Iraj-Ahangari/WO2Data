# Workorder2data

## What it is
A conversion (ETL) tool that turns raw CMMS maintenance text — work order descriptions, technician comments, work reports — into structured records following **ISO 14224:2016** (*Petroleum, petrochemical and natural gas industries — Collection and exchange of reliability and maintenance data for equipment*, 3rd edition).

Messy free text in, ISO 14224 taxonomy records out.

## Scope
**In scope**
- Input: CSV (configurable column mapping) or plain text (one work order per file, or many split on a `---` line).
- Output: JSONL (master) plus a flat CSV view in which unknown cells are blank for a human to fill in.
- All equipment classes defined in the standard (Annex A, about 60 classes), delivered in phases:
  - Phase 1: topside (rotating, mechanical, electrical, safety and control).
  - Phase 2: subsea, well completion, drilling, well intervention, marine.
- Fields (v1):
  - Failure (Table 6): equipment tag, failure date, failure mode, failure mechanism, failure cause, subunit failed, maintainable item failed, detection method, effect on function, remarks.
  - Maintenance (Table 8): maintenance category, maintenance activity, subunit and maintainable item maintained, start date, impact on plant operations, man-hours and downtime when stated.
- Two entry points over one library function (`convert(text) -> list[Record]`): a batch CLI and a single-text CLI.
- Hybrid extraction: deterministic parsing and validation in code, semantic classification by a model.
- v1 model backend: Anthropic, behind a small interface so an OpenAI-compatible (local or cloud) adapter can be added later.

**Out of scope (separate tools, later)**
- Analysis, KPIs, dashboards, root-cause analysis, reliability calculations.
- Writing back to a CMMS.
- Full equipment hierarchy (installation → plant → system), operational phase, safety/environment/asset impact, DU/DD/SU/SD classes.
- Languages other than English.

## Principles
1. **The standard is the only source of taxonomy values.** They are transcribed from `Resources/ISO-14224.pdf` into `taxonomy/*.csv`, never recalled from model memory and never hardcoded in code.
2. **Never guess.** A field is a valid taxonomy code or `unknown`. Every non-unknown value carries a verbatim evidence quote from the source text. Unknowns produce warnings so the user can fill them in.
3. **Deterministic where possible.** CMMS metadata (tag, dates) is parsed, not predicted. Every model output is validated against the taxonomy; invalid values are retried once, then become `unknown`.
4. **The CMRP validates.** Domain judgment on taxonomy rows and gold labels belongs to the user.
5. **Class-agnostic code, taxonomy as data.** Adding an equipment class means adding verified CSV rows, not code.

## How we start
- Taxonomy first: transcribe from PDF page images with `source_page` and `verified=false`, then the user verifies class by class.
- Synthetic, label-first test data (about 5 records per class, weighted toward common classes, floor of 3 per class), a frozen test split, and about 60 gold records reviewed by the user.
- Measure per-field precision and recall; a wrong value is worse than `unknown`.

## Open source and the standard
The code is MIT-licensed and public. ISO 14224 itself is copyrighted, so this repo does **not** ship the standard or any taxonomy tables transcribed from it. Each user supplies their own licensed copy of the standard and builds `taxonomy/*.csv` from it (see `taxonomy/README.md`). A tiny fictional taxonomy in `tests/fixtures/taxonomy/` lets the code and tests run without it.

See `docs/schema.md` for the output schema and `docs/plan.md` for the phased plan.
