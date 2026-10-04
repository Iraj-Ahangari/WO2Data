# Output schema (proposal, v0)

Status: draft for the user's review. Field list follows ISO 14224:2016 Table 6 (failure data) and Table 8 (maintenance data). **No taxonomy values appear here by design**; they are supplied by `taxonomy/*.csv` (see below).

## Field wrapper
Every extracted field is a `Field`:

| Key | Meaning |
|---|---|
| `value` | A taxonomy code (or free value for dates/numbers/remarks) or the literal `unknown` |
| `evidence` | Verbatim quote from the source text supporting the value; `null` when `unknown` |
| `taxonomy_verified` | `true` only if the matched taxonomy row has `verified=true` |

## Record
| Field | Notes |
|---|---|
| `record_id` | Unique per output record |
| `source_id` | Work order ID; several records can share one |
| `source_row` / `source_offset` | Row in the CSV, or offset in the text file |
| `record_type` | `failure` \| `maintenance` \| `both` |
| `needs_review` | `true` if any warning needs human action |
| `warnings[]` | `{field, reason}`; reasons: `not_stated`, `conflicting`, `not_in_taxonomy`, `unverified_taxonomy`, `class_unknown` |
| `meta` | extractor, model, prompt version, taxonomy version, synthetic flag |

### Equipment
| Field | ISO source | Taxonomy file |
|---|---|---|
| `tag` | Table 5/6 | none (parsed from text or CSV column) |
| `equipment_class` | Table A.4 (level 6) | `equipment_classes.csv` |
| `subunit` | Annex A, per class | `subunits.csv` |
| `maintainable_item` | Annex A, per class | `maintainable_items.csv` |

### Failure (Table 6)
| Field | ISO source | Taxonomy file |
|---|---|---|
| `failure_date` | Table 6 | none (parsed) |
| `failure_mode` | B.6–B.14 (overview B.15), scoped by class/category | `failure_modes.csv` |
| `failure_mechanism` | Table B.2 | `failure_mechanisms.csv` |
| `failure_cause` | Table B.3 | `failure_causes.csv` |
| `detection_method` | Table B.4 | `detection_methods.csv` |
| `effect_on_function` | Table 6, note c: critical \| degraded \| incipient | small enum, to be confirmed against the PDF |
| `remarks` | Table 6 | free text |

### Maintenance (Table 8)
| Field | ISO source | Taxonomy file |
|---|---|---|
| `maintenance_category` | §9.6.2: corrective \| preventive | small enum, to be confirmed against the PDF |
| `maintenance_activity` | Table B.5 | `maintenance_activities.csv` |
| `subunit_maintained` | Annex A | `subunits.csv` |
| `maintainable_item_maintained` | Annex A | `maintainable_items.csv` |
| `maintenance_start_date` | Table 8 | none (parsed) |
| `impact_on_plant_ops` | Table 8: zero \| partial \| total | small enum, to be confirmed against the PDF |
| `man_hours_total` | Table 8 | none; only if stated |
| `downtime_hours` | Table 8 | none; only if stated |

Deferred from v1: equipment hierarchy above equipment unit, operational phase, failure impact on safety/environment/assets, DU/DD/SU/SD, spare-part location, interval.

## Taxonomy files — **the user/standard supplies these values**
All in `taxonomy/`, drafted from PDF page images, then verified by the user. Common columns:

`code`, `name`, `description`, `applies_to` (equipment class or category), `source_table`, `source_page`, `verified`

| File | Source tables | Needed by |
|---|---|---|
| `equipment_classes.csv` | A.4 (+ category tables) | Stage 1, all validation |
| `subunits.csv` | Annex A per-class subdivision tables | `subunit*` fields |
| `maintainable_items.csv` | Annex A per-class subdivision tables | `maintainable_item*` fields |
| `failure_modes.csv` | B.6–B.14, class mapping from B.15 | `failure_mode` |
| `failure_mechanisms.csv` | B.2 | `failure_mechanism` |
| `failure_causes.csv` | B.3 | `failure_cause` |
| `detection_methods.csv` | B.4 | `detection_method` |
| `maintenance_activities.csv` | B.5 | `maintenance_activity` |

Automated cross-checks (run in tests): every B.15 code appears in a B.6–B.14 table; every subunit and maintainable item maps to a class in `equipment_classes.csv`; no duplicate `(code, applies_to)`; every row has `source_page`.

## Open points for the user
- Confirm the field list against Tables 6 and 8 (which are mandatory in your practice).
- Confirm the exact enum values for `effect_on_function`, `maintenance_category` and `impact_on_plant_ops` from the PDF.
- Decide whether `remarks` stays free text or is dropped from v1.
