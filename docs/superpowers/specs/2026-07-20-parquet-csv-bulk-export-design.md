# Parquet + CSV bulk exports (#161)

**Date:** 2026-07-20
**Status:** approved (autonomous — maintainer reviews at the PR), not yet implemented
**Delivers:** per-table **Parquet** files (the columnar lingua franca — DuckDB/pandas/Polars can
query them directly over HTTP) and a **CSV** bundle (`bids-csv.zip`, for the Excel/vendor
audience), published to the `toronto-bids-data` release beside `bids.json` / `bids.sqlite`, with
their sizes folded into the existing `manifest.json` (#168).

Builds directly on #168: it reuses `db.EXPORT_TABLES` and extends `manifest.json` rather than
inventing its own.

## 1. Source: the flat SQLite tables, not the nested JSON

`build_export_document` produces a *nested* document (awards/bids under their solicitation). Parquet
and CSV are inherently *tabular*, so their natural source is the flat store tables — one file per
table, keys intact, an analyst joining `award.document_number` to `solicitation.document_number`
themselves. This is the shape DuckDB/pandas want and the shape `bids.sqlite` already exposes; the
bulk formats are a columnar mirror of the same 18 tables `schema.json` documents.

The table set is **`db.EXPORT_TABLES`** (the #168 constant) — no drift from `schema.json` or
`counts()`.

## 2. Column curation — two lean rules

Each table is dumped as-is except:

1. **Giant blob columns are excluded** — they would dominate file size and are not analytical:
   `ariba_posting.raw_json` (verbatim JSON blobs) and `background_pdf.text` (full pdftotext of
   3,000+ reports). The nested JSON export already drops both for the same reason.
2. **Server-local paths are excluded** — any column named `local_path` (a filesystem path on the
   home server; the JSON export drops it too).

Everything else stays — including `id` primary keys, `sha256`/`crc32` checksums, and City
identifiers (`odata_id`). In *flat* tabular form the ids are useful join keys (unlike the nested
JSON, where they are redundant and dropped), and public-PDF checksums are not sensitive. The rule
is deliberately small and testable: a module constant `_EXCLUDE_COLUMNS: set[tuple[str, str]]`
plus the `local_path`-by-name rule.

## 3. Architecture — `export/tabular_export.py`

Pure-ish builders over the connection, mirroring the export seam. pyarrow is a new core
dependency (there is no stdlib Parquet writer); CSV uses stdlib `csv` + `zipfile`.

- **`_read_table(conn, table) -> tuple[list[str], list[tuple]]`** (helper): resolve the kept
  columns from `PRAGMA table_info` minus `_EXCLUDE_COLUMNS`/`local_path`, then
  `SELECT <cols> FROM <table> ORDER BY 1` (order by the first output column — deterministic
  content within a DB). Returns column names + rows.
- **`write_parquet_files(conn, out_dir) -> list[Path]`**: for each table in `EXPORT_TABLES`,
  build a `pyarrow.table({col: [values]})` (Arrow infers each column's type from its values; an
  all-NULL column becomes Arrow `null` type, which Parquet stores fine) and
  `pyarrow.parquet.write_table` to `out_dir/<table>.parquet`. Returns the written paths.
- **`write_csv_zip(conn, out_path) -> Path`**: open a `zipfile.ZipFile(out_path, "w",
  ZIP_DEFLATED)` and write one `<table>.csv` entry per table — header row of column names, then
  rows via `csv.writer`; `None` renders as an empty field (Python csv default). Returns the path.

Both iterate `EXPORT_TABLES`, so all 18 tables are always present.

### Type handling (Parquet)

sqlite3 returns native Python objects (`str`/`int`/`float`/`None`/`bytes`), and the store's
columns are type-consistent by affinity (e.g. `award_amount_numeric` is REAL-or-NULL, text
columns are TEXT-or-NULL), so per-column Arrow inference is safe. A column that is entirely NULL
infers Arrow `null` type — acceptable for an archival dump. No declared-type → Arrow-type mapping
is needed; inference over actual values is correct and simpler. (Tested against a REAL-with-NULLs
column and an all-NULL column so the inference path is pinned.)

## 4. Wiring

- **`tb export`** writes the bulk formats beside `bids.json` / `schema.json`: the per-table
  `<table>.parquet` files and `bids-csv.zip`, all in `EXPORT_DIR`. Sub-second for this corpus, so
  it is always-on (no opt-out flag — YAGNI).
- **Nightly `_export` step** (`_cmd_nightly`) does the same, so the production path emits them
  (the nightly calls the builders directly, as it does for `schema.json`).
- **`publish-data.sh`**:
  - Require the artifacts (`bids-csv.zip` and at least one `*.parquet`) — a missing bulk export is
    a publish gap, surfaced with `fail`, like `schema.json`.
  - Add `"$EXPORT_DIR"/*.parquet` and `"$EXPORT_DIR/bids-csv.zip"` to the `ASSETS` array, so both
    the `latest` release and the monthly snapshot carry them (per-table Parquet as **individual
    release assets** — directly queryable over HTTP: `SELECT * FROM
    'https://…/award.parquet'`).
  - Extend the `tb manifest` invocation to include the parquet files + the csv zip, so
    `manifest.json` states their sizes. Row counts already live in `schema.json` (#168) — the
    frontend joins the two.

## 5. Dependency

`pyarrow` is added to `[project].dependencies` in `scrapers/pyproject.toml` (the nightly/publish
path needs it, so it is core, not an extra). `uv.lock` is re-locked and committed — CI runs
`uv sync --locked` and fails otherwise. No build step (pyarrow ships prebuilt wheels), no runtime
network (unlike a duckdb sqlite-scanner extension), and it is the format the pandas/Arrow
ecosystem reads natively.

## 6. Determinism / size

- `ORDER BY 1` gives stable row order within a DB; Parquet files are data artifacts (not diffed),
  so pyarrow's internal metadata is not a concern.
- Excluding `raw_json`/`text` keeps the bulk export lean; without it `background_pdf` alone would
  carry the full text of 3,000+ PDFs.

## 7. Testing

Fixture-based, offline (pyarrow is a core dep, so tests import it directly — no skip guard):

- **`write_csv_zip`**: produces a zip with one `<table>.csv` per `EXPORT_TABLES`; a seeded
  table's csv has the header + a data row; a NULL column renders as an empty field; an excluded
  column (`ariba_posting.raw_json`) is absent from the header.
- **`write_parquet_files`**: writes one `<table>.parquet` per table; reading a seeded table back
  with `pyarrow.parquet.read_table` yields the inserted rows; a REAL-with-NULL column and a
  TEXT column round-trip; `background_pdf.text` / `ariba_posting.raw_json` / any `local_path`
  column is absent from the Parquet schema.
- **`_read_table`**: excludes the blob/path columns; `ORDER BY 1` gives stable order.
- **CLI**: `tb export` writes the parquet files + `bids-csv.zip` into the export dir (a small
  seeded DB, asserting the files exist).
- **Nightly**: a clean run writes `bids-csv.zip` and at least one `*.parquet` (extends the #168
  nightly assertion).
- **`publish-data.sh`** dry-run: the upload list includes the parquet files + csv zip; a missing
  bulk export fails loudly; `manifest.json` lists their sizes.

## 8. Out of scope

- **XLSX** — redundant with CSV for opening in Excel, and another dependency (openpyxl). CSV
  covers the non-technical audience.
- A single "flattened" one-table export — the data is relational; per-table with keys is the
  faithful tabular shape.
- Nested/JSON changes — `bids.json` and its shape are untouched.
- Publishing Parquet partitioned/compressed beyond pyarrow's default (Snappy) — default is fine.

## 9. Recording

On completion, comment on #161 with the published asset URLs (the per-table Parquet + `bids-csv.zip`
on the `latest` release) and note that `manifest.json` now carries their sizes.
