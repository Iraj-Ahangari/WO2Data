# taxonomy/

This folder is where the ISO 14224:2016 tables live as CSVs. **The CSVs are not part of the public repo** (git-ignored): the standard is copyrighted and each user builds them from their own licensed copy. Only this README is tracked. A fictional example in the same format is in `tests/fixtures/taxonomy/`.

On the maintainer's machine the CSVs were transcribed from `Resources/ISO-14224.pdf` page images. **Every row is `verified=false` until a domain expert (CMRP) sets it to `true`.**

`source_page` is the **PDF page index** (open `Resources/ISO-14224.pdf` at that page). The printed page number is the PDF index minus 6.

| File | Source | Rows | Notes |
|---|---|---|---|
| `equipment_classes.csv` | Table A.4 (pdf 58–66) | 108 | `class_id` is a slug of the name. `code` is **not unique** (W1, W2, W3, WC each appear for coiled tubing, snubbing and wireline). |
| `equipment_categories.csv` | Table A.4 + B.6–B.14 | 11 | `failure_mode_table` says which of B.6–B.14 applies; blank for Utilities and Auxiliaries (no table in the standard). |
| `failure_mechanisms.csv` | Table B.2 (pdf 185–186) | 44 | Two levels (`level`, `parent_code`). Level-1 rows have no description in the standard. |
| `failure_causes.csv` | Table B.3 (pdf 187–188) | 26 | Same structure as mechanisms. |
| `detection_methods.csv` | Table B.4 (pdf 189) | 11 | `activity_group` is the right-hand column of the table. |
| `maintenance_activities.csv` | Table B.5 (pdf 190) | 12 | `use`: `C` typically corrective, `P` typically preventive (`C;P` both). |
| `subunits.csv` | Annex A subdivision tables for the topside classes (pdf 69–130) | 134 | `subunit_id` = `<class_id>.<n>`. Only classes that have a subdivision table. |
| `maintainable_items.csv` | Same tables | 810 | One row per item under a subunit, in table order. |
| `failure_modes.csv` | Tables B.6–B.9 (pdf 193–200) | 119 | One row per table row: a code can appear on several rows with different explanations. `applies_to` lists the class codes marked X. |
| `failure_mode_class_aliases.csv` | Table B.9 column headers | 2 | B.9 uses two column codes that are not class codes in A.4; this maps them to a class. |
| `footnotes.csv` | Footnotes and notes of the tables above | 87 | Rows reference them by letter in `footnote_refs`. |

Phase 1b covers the topside categories only (rotating, mechanical, electrical, safety and control). Not yet transcribed: the failure-mode tables for the other categories (B.10–B.14), their Annex A tables, the B.15 overview, the per-class "type classification" tables, and the equipment-specific data tables.

## Verification checklist for the user
1. Open each page and compare 10 random rows per file; then confirm the rest.
2. Check characters that are easy to misread: class codes with the letter O vs digit 0 (`CO`, `NO`, `BO`, `TO`, `OC`, `OI`), and the mixed-case code `Am` (anchor windlasses; printed that way in the PDF).
3. Flip `verified` to `true` only after you have checked the row.

## Points that may need your judgment
- Of the 44 topside classes, only 27 have a subdivision table and a failure-mode column in the standard; the same 27 in both. The other 17 have neither, so subunit and failure mode cannot be validated for them.
- The control logic units table lists "No subdivision" for two of its subunits (system bus, power supply).
- Utilities (A.2.11) and Auxiliaries (A.2.12) have no failure-mode table in B.6–B.14.
- B.4 prose says "nine categories", but the table has 11 codes; the table is used.
- `Flare ignition` (FI, safety and control) and `Flare ignition equipment` (FE, utilities) appear as two classes.

## Building the tables from your own copy of the standard
1. Put your licensed PDF at `Resources/ISO-14224.pdf` (git-ignored). Use the 2016 edition; page numbers below are PDF page indexes for that edition.
2. Do not rely on `pdftotext`: the text layer is garbled (ligatures, digits, brackets). Render the pages to images (`pdftoppm -r 110 -png -f N -l M`) and transcribe from the images.
3. Create one CSV per file in the table above with the columns shown in `tests/fixtures/taxonomy/*.csv`, including `source_table`, `source_page` and `verified=false` on every row.
4. Run `uv run pytest`; the structural checks run on your tables and the ISO-specific snapshot checks (row counts etc.) activate automatically.
5. Verify rows against the PDF and set `verified=true` only on rows you checked.
