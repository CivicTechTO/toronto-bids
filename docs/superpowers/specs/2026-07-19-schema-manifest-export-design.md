# Publish schema.json + manifest.json for the frontend /data/ dictionary (#168)

**Date:** 2026-07-19
**Status:** approved (autonomous — maintainer reviews at the PR), not yet implemented
**Delivers:** two small generated JSON assets in the `toronto-bids-data` release beside `bids.json`
— `schema.json` (an exhaustive column-level data dictionary generated from the SQLite, with
authoritative per-table row counts) and `manifest.json` (published-artifact file sizes) — so the
frontend `/data/` page renders a real dictionary + sizes instead of a "coming soon" note, and the
analyst downloading `bids.sqlite` no longer has to `.schema` the DB and guess.

Backend dependency for frontend #12 / toronto-bids#168.

## 1. Why the backend, not the frontend

The frontend already derives record counts and observed coded-column domains from `bids.json`.
Three things can only be authoritative if generated at export time, from the SQLite itself, so
they cannot drift from the real schema:

1. **A column-level dictionary** — every table, every column, its declared type and nullability
   (and a curated one-line gloss where it adds value). Hand-maintaining this on the frontend
   would drift from the schema, which is the problem.
2. **Per-table row counts** — the SQLite `COUNT(*)` is ground truth; the frontend's counts are
   logical/derived.
3. **File sizes** of each published artifact — only knowable at publish time.

## 2. Architecture — two pure builders behind the export seam

The export seam's rule (`export/document.py`) is: shaping logic is a pure function over the
`conn`, deterministic given `generated_at`; a serializer/publisher is a thin wrapper. Two new
builders in **`export/schema_export.py`** follow it exactly.

### `build_schema_document(conn, generated_at=None) -> dict`

Pure, deterministic, no file I/O. Shape:

```json
{
  "generated_at": "2026-07-19T05:30:00Z",
  "tables": {
    "solicitation": {
      "row_count": 7444,
      "columns": [
        { "name": "document_number", "type": "TEXT", "nullable": false,
          "description": "10-digit normalized competitive identifier (the primary key)" },
        { "name": "status", "type": "TEXT", "nullable": true,
          "enum": ["Awarded", "Cancelled", "Open"] },
        { "name": "first_seen", "type": "TEXT", "nullable": false }
      ]
    }
  }
}
```

- **Tables:** a canonical ordered list — the 18 data/bookkeeping tables (the same set
  `db.counts` enumerates, kept as one shared constant `db.EXPORT_TABLES` so the two cannot
  drift). Ordered for a deterministic document.
- **`type`:** the declared column type from `PRAGMA table_info().type`, verbatim (`TEXT`,
  `INTEGER`, `REAL`). Empty declared type → omit the key rather than emit `""`.
- **`nullable`:** `not PRAGMA table_info().notnull`. A PK column reports `notnull=0` in SQLite
  yet is not nullable — acceptable: the dictionary reflects the declared constraint, and the PK
  is separately identifiable (see `primary_key`). We do **not** invent nullability beyond the
  declaration.
- **`primary_key`:** `true` on a column with `PRAGMA table_info().pk > 0` (omitted otherwise) —
  cheap, and it disambiguates the PK-nullability caveat above for the analyst.
- **`row_count`:** `SELECT COUNT(*)` per table.
- **`description`** (optional): from the curated dictionary (§4), only where present.
- **`enum`** (optional): observed distinct values (§3), only for declared coded columns.

### `build_manifest_document(files, generated_at=None) -> dict`

Pure over a list of `(name, path)` (or `Path`) inputs; stats each existing file. Shape:

```json
{ "generated_at": "…", "artifacts": [
  { "name": "bids.json",     "bytes": 25165824 },
  { "name": "bids.json.gz",  "bytes": 3145728 },
  { "name": "bids.sqlite",   "bytes": 41943040 }
] }
```

A named file that does not exist is **omitted** from the `artifacts` list (publish only ever
passes files it has just written/verified; a missing one there is already a `fail` upstream).
`bytes` is `Path.stat().st_size`. Row counts
live **only** in `schema.json` — one generated source, per the issue.

## 3. Enum generation — observed, for a declared set

A module-level constant declares which `(table, column)` pairs are coded:

```python
_ENUM_COLUMNS = {
    ("solicitation", "status"),
    ("solicitation", "rfx_type"),
    ("bid", "hst_basis"),
    ("award", "award_amount_verdict"),
    ("noncompetitive", "contract_amount_verdict"),
    # … the coded columns, curated list
}
```

For each, the enum is `SELECT DISTINCT <col> FROM <table> WHERE <col> IS NOT NULL ORDER BY <col>`
— the **observed domain**, auto-staying in sync as the City introduces new values (the exact
drift #168 exists to prevent; matches the issue's "observed value set"). A dirty upstream value
appearing in the list is honest — it is what is in the data. An empty result (no rows, or all
NULL) → omit the `enum` key.

Declaring the columns (rather than auto-detecting "low-cardinality TEXT") keeps it intentional:
`title` has few-hundred distinct values but is not an enum. The declared set is small and
reviewed.

## 4. Curated descriptions — high-value subset

Descriptions live in a data file **`toronto_bids/data/schema_dictionary.toml`**, loaded like
`amount_labels.toml`:

```toml
["solicitation.document_number"]
description = "10-digit normalized competitive identifier (the primary key)"

["award.award_amount"]
description = "The City's published amount string, verbatim and NOT summable — aggregate award_amount_numeric instead"

["award.award_amount_numeric"]
description = "Machine-parsed CAD amount; NULL when the raw string is not a single CAD amount"
```

Scope: the **high-value subset** — coded columns, key identifiers/foreign keys, and the
documented traps (`award_amount` vs `award_amount_numeric`, `title` NULL = no title published,
`source` dual-provenance, `title_source` provenance, `bid_price` ≠ `award_amount`,
`value_confidential`). ~40–60 entries, mined from schema.sql's existing inline comments. Columns
with no entry ship with `type`/`nullable`/`primary_key` only — the dictionary is still exhaustive
in coverage (every column appears), just not every column is glossed.

The keys are `"<table>.<column>"`. A key naming a table/column that no longer exists is a stale
entry: a unit test asserts every dictionary key resolves to a real column, so a rename surfaces
loudly (the schema-drift ethos).

## 5. Wiring

- **`export/json_export.py`** gains `export_schema(conn, out_path, generated_at=None) -> Path`
  (thin serializer over `build_schema_document`, mirroring `export_json`).
- **`tb export`** writes `schema.json` beside `bids.json` in the same command (it holds the conn;
  `generated_at` shared so both agree). Nightly's existing `_export` step picks it up for free —
  no new nightly surface, no new browser/network anything.
- **New CLI `tb manifest <file…> --out <path>`** — builds `manifest.json` from the given files
  via `build_manifest_document`. Called by `publish-data.sh` at publish time (after the gzip),
  so the sizes are the actual uploaded bytes.
- **`deploy/publish-data.sh`**: after gzip, generate `manifest.json`
  (`uv run tb manifest "$JSON" "$GZ" "$SQLITE" --out "$EXPORT_DIR/manifest.json"`), then add
  `schema.json` and `manifest.json` to the `ASSETS` array so both the `latest` release and the
  monthly snapshot carry them. `schema.json` is written by the nightly `tb export` already; the
  script does not regenerate it.

## 6. Determinism / faithfulness

- Both builders are ORDER BY / stable-iteration throughout, so byte-identical given the same DB
  and `generated_at` (the `build_export_document` contract).
- `schema.json`'s `generated_at` comes from the same value `tb export` passes `bids.json`, so a
  release's three JSON assets share one timestamp.

## 7. Testing

Fixture-based, offline (the project's dev loop — no network):

- **`build_schema_document`** over an in-memory DB seeded via `db.connect`/`db.init` + a few
  `upsert_row`s: every table present and ordered; a known column reports the right
  `type`/`nullable`/`primary_key`; `row_count` matches inserted rows; a declared enum column
  yields its observed sorted distinct set; a column with a dictionary entry carries its
  `description`; a column without one omits `description`/`enum`.
- **Enum**: an enum column that is all-NULL/empty omits the `enum` key; a new distinct value
  inserted appears in the set (proves it is observed, not curated).
- **`build_manifest_document`**: three temp files → three `{name, bytes}` with correct sizes and
  order; a non-existent named file is omitted.
- **Dictionary integrity**: every key in `schema_dictionary.toml` resolves to a real
  `(table, column)` in the reference schema — guards against a stale gloss after a rename.
- **CLI `tb manifest`**: writes valid JSON to `--out` for given files.
- No live-run gate needed (no network, no browser, no matching heuristic) — but the plan's final
  step runs `tb export` + `tb manifest` against a real synced DB once and eyeballs the two JSONs
  for shape sanity.

## 8. Out of scope

- Parquet/CSV bulk exports (#161 — the next issue; this ships the manifest #161 also wants, so
  #161 extends `build_manifest_document`'s input list rather than inventing its own).
- Rendering the dictionary — that is the frontend (#12).
- Curated per-value enum glosses (the "observed + curated gloss" option was not taken; enums are
  bare observed value lists).
- Descriptions for every column (the high-value subset was chosen).

## 9. Recording

On completion, comment on #168 with the published asset URLs (schema.json / manifest.json on the
`latest` release) and note that #161 will extend the manifest with the new bulk-format sizes.
