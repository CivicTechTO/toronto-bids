# Parquet + CSV Bulk Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish per-table Parquet files (HTTP-queryable columnar data) and a `bids-csv.zip` bundle to the `toronto-bids-data` release beside `bids.json`, with their sizes folded into the existing `manifest.json` (#161).

**Architecture:** A new `export/tabular_export.py` dumps the 18 flat store tables (reusing `db.EXPORT_TABLES` from #168) to per-table `<table>.parquet` (pyarrow) and a `bids-csv.zip` (stdlib csv+zipfile), excluding two giant blob columns and server paths. `tb export`/nightly emit them; `publish-data.sh` uploads them and extends the manifest.

**Tech Stack:** Python 3.12, `pyarrow` (new core dep), stdlib `csv`/`zipfile`/`sqlite3`, pytest.

## Global Constraints

- The table set is `db.EXPORT_TABLES` (the #168 shared constant) — no drift from `schema.json`/`counts()`.
- Excluded columns: `ariba_posting.raw_json`, `background_pdf.text` (giant blobs), and any column named `local_path` (server filesystem path). Everything else — ids, keys, checksums, `odata_id` — is kept.
- Deterministic content: each table read `ORDER BY 1` (first kept column).
- Rows read as plain tuples (`tuple(r)`), never sqlite3.Row, so csv and pyarrow both consume them cleanly.
- `pyarrow` is a core dependency (nightly/publish needs it); `uv.lock` must be re-locked and committed (CI runs `uv sync --locked`).
- Tests are offline/fixture-based; pyarrow is a core dep so tests import it directly (no skip guard).
- `bids.json`'s shape is untouched.

---

### Task 1: Add pyarrow dependency and re-lock

**Files:**
- Modify: `scrapers/pyproject.toml`
- Modify: `scrapers/uv.lock` (regenerated)

**Interfaces:**
- Produces: `pyarrow` importable in the project environment.

- [ ] **Step 1: Add pyarrow to core dependencies**

In `scrapers/pyproject.toml`, add `pyarrow` to `[project].dependencies`:

```toml
dependencies = [
    "httpx",
    "lxml>=6.1.1",
    "pdfplumber>=0.11.10",
    "pyarrow>=17",
]
```

- [ ] **Step 2: Re-lock and sync**

Run:
```bash
cd scrapers && uv sync
```
Expected: resolves and installs pyarrow; `uv.lock` is updated.

- [ ] **Step 3: Verify the locked env is consistent and pyarrow imports**

Run:
```bash
cd scrapers && uv sync --locked && uv run python -c "import pyarrow, pyarrow.parquet; print('pyarrow', pyarrow.__version__)"
```
Expected: `uv sync --locked` succeeds (no "lockfile out of date"), and the version prints.

- [ ] **Step 4: Confirm the existing suite still passes under the new env**

Run: `cd scrapers && uv run pytest -q`
Expected: all tests pass (baseline unchanged).

- [ ] **Step 5: Commit**

```bash
git add scrapers/pyproject.toml scrapers/uv.lock
git commit -m "build: add pyarrow core dependency for Parquet bulk export (#161)"
```

---

### Task 2: `_read_table` + `write_csv_zip`

**Files:**
- Create: `scrapers/toronto_bids/export/tabular_export.py`
- Test: `scrapers/tests/test_tabular_export.py`

**Interfaces:**
- Consumes: `db.EXPORT_TABLES`.
- Produces:
  - `tabular_export._EXCLUDE_COLUMNS: set[tuple[str, str]]`
  - `tabular_export._read_table(conn, table) -> tuple[list[str], list[tuple]]`
  - `tabular_export.write_csv_zip(conn, out_path) -> Path`

- [ ] **Step 1: Write the failing csv tests**

Create `scrapers/tests/test_tabular_export.py`:

```python
import csv
import io
import zipfile

from toronto_bids.export.tabular_export import _read_table, write_csv_zip
from toronto_bids.models import AribaPosting, Award, Solicitation
from toronto_bids.store import db


def test_read_table_excludes_blob_and_path_columns(conn):
    cols, _ = _read_table(conn, "ariba_posting")
    assert "raw_json" not in cols          # giant blob excluded
    assert "rfx_id" in cols                # keys kept
    cols_pdf, _ = _read_table(conn, "background_pdf")
    assert "text" not in cols_pdf          # giant blob excluded
    assert "local_path" not in cols_pdf    # server path excluded
    assert "url" in cols_pdf


def test_read_table_orders_by_first_column(conn):
    db.upsert_row(conn, Solicitation(document_number="2000000002", source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000001", source="odata"), overwrite=True)
    conn.commit()
    cols, rows = _read_table(conn, "solicitation")
    assert cols[0] == "document_number"
    assert [r[0] for r in rows] == ["1000000001", "2000000002"]


def test_write_csv_zip_has_one_csv_per_table(conn, tmp_path):
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    assert out == tmp_path / "bids-csv.zip"
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == {f"{t}.csv" for t in db.EXPORT_TABLES}


def test_csv_has_header_and_null_is_empty(conn, tmp_path):
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Acme",
                              award_amount="1000", source="odata"), overwrite=True)
    conn.commit()
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    with zipfile.ZipFile(out) as zf:
        text = zf.read("award.csv").decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    header = reader[0]
    assert "supplier_name_raw" in header
    row = reader[1]
    # award_date was never set -> empty field, not the string "None"
    assert row[header.index("award_date")] == ""
    assert row[header.index("supplier_name_raw")] == "Acme"


def test_excluded_column_absent_from_csv_header(conn, tmp_path):
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    with zipfile.ZipFile(out) as zf:
        header = zf.read("ariba_posting.csv").decode("utf-8").splitlines()[0]
    assert "raw_json" not in header
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_tabular_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.export.tabular_export'`.

- [ ] **Step 3: Implement `_read_table` + `write_csv_zip`**

Create `scrapers/toronto_bids/export/tabular_export.py`:

```python
import csv
import io
import zipfile
from pathlib import Path

from toronto_bids.store import db

# Columns dropped from the bulk tabular export. The two blobs would dominate file size and are
# not analytical (the nested JSON export drops them too); `local_path` is a server filesystem
# path. Everything else — ids, keys, checksums, City identifiers — is kept as a join key.
_EXCLUDE_COLUMNS = {
    ("ariba_posting", "raw_json"),
    ("background_pdf", "text"),
}


def _kept_columns(conn, table: str) -> list[str]:
    return [
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
        if (table, row[1]) not in _EXCLUDE_COLUMNS and row[1] != "local_path"
    ]


def _read_table(conn, table: str) -> tuple[list[str], list[tuple]]:
    """The kept columns and rows of one table, ORDER BY the first column (deterministic).

    Rows are plain tuples so both csv.writer and pyarrow consume them without sqlite3.Row quirks.
    """
    cols = _kept_columns(conn, table)
    col_list = ", ".join(cols)
    rows = [tuple(r) for r in conn.execute(f"SELECT {col_list} FROM {table} ORDER BY 1")]
    return cols, rows


def write_csv_zip(conn, out_path) -> Path:
    """One `<table>.csv` per EXPORT_TABLES, bundled into a single zip. NULL renders as an empty
    field (csv default). For the non-technical / Excel audience (#161)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in db.EXPORT_TABLES:
            cols, rows = _read_table(conn, table)
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(cols)
            writer.writerows(rows)
            zf.writestr(f"{table}.csv", buf.getvalue())
    return out_path
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_tabular_export.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/tabular_export.py scrapers/tests/test_tabular_export.py
git commit -m "feat(export): flat-table reader + bids-csv.zip bundle (#161)"
```

---

### Task 3: `write_parquet_files`

**Files:**
- Modify: `scrapers/toronto_bids/export/tabular_export.py`
- Test: `scrapers/tests/test_tabular_export.py`

**Interfaces:**
- Consumes: `_read_table` (Task 2); `pyarrow` (Task 1).
- Produces: `tabular_export.write_parquet_files(conn, out_dir) -> list[Path]`.

- [ ] **Step 1: Write the failing parquet tests**

Append to `scrapers/tests/test_tabular_export.py`:

```python
import pyarrow.parquet as pq

from toronto_bids.export.tabular_export import write_parquet_files


def test_write_parquet_one_file_per_table(conn, tmp_path):
    paths = write_parquet_files(conn, tmp_path)
    names = {p.name for p in paths}
    assert names == {f"{t}.parquet" for t in db.EXPORT_TABLES}
    for p in paths:
        assert p.exists()


def test_parquet_roundtrips_rows_and_types(conn, tmp_path):
    # A REAL-with-NULL column (award_amount_numeric) and a TEXT column round-trip.
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Acme",
                              award_amount="1000", award_amount_numeric=1000.0,
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="1000000002", supplier_name_raw="Beta",
                              award_amount="kj", source="odata"), overwrite=True)  # numeric NULL
    conn.commit()
    write_parquet_files(conn, tmp_path)
    table = pq.read_table(tmp_path / "award.parquet").to_pylist()
    by_supplier = {r["supplier_name_raw"]: r for r in table}
    assert by_supplier["Acme"]["award_amount_numeric"] == 1000.0
    assert by_supplier["Beta"]["award_amount_numeric"] is None


def test_parquet_omits_excluded_columns(conn, tmp_path):
    write_parquet_files(conn, tmp_path)
    ap = pq.read_table(tmp_path / "ariba_posting.parquet")
    assert "raw_json" not in ap.column_names
    bp = pq.read_table(tmp_path / "background_pdf.parquet")
    assert "text" not in bp.column_names
    assert "local_path" not in bp.column_names
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_tabular_export.py -k parquet -v`
Expected: FAIL — `ImportError: cannot import name 'write_parquet_files'`.

- [ ] **Step 3: Implement `write_parquet_files`**

In `scrapers/toronto_bids/export/tabular_export.py`, add the imports at the top:

```python
import pyarrow as pa
import pyarrow.parquet as pq
```

and the function:

```python
def write_parquet_files(conn, out_dir) -> list[Path]:
    """One `<table>.parquet` per EXPORT_TABLES in out_dir. Arrow infers each column's type from
    its values (an all-NULL column becomes Arrow null type, which Parquet stores fine). For the
    DuckDB/pandas/Polars audience — the files are individually HTTP-queryable (#161)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in db.EXPORT_TABLES:
        cols, rows = _read_table(conn, table)
        data = {col: [row[i] for row in rows] for i, col in enumerate(cols)}
        arrow_table = pa.table(data)
        path = out_dir / f"{table}.parquet"
        pq.write_table(arrow_table, path)
        written.append(path)
    return written
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_tabular_export.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/tabular_export.py scrapers/tests/test_tabular_export.py
git commit -m "feat(export): per-table Parquet files via pyarrow (#161)"
```

---

### Task 4: Wire `tb export` and nightly `_export`

**Files:**
- Modify: `scrapers/toronto_bids/cli.py`
- Test: `scrapers/tests/test_cli.py`, `scrapers/tests/test_nightly.py`

**Interfaces:**
- Consumes: `write_parquet_files`, `write_csv_zip` (Tasks 2-3).
- Produces: `tb export` and the nightly export step write the parquet files + `bids-csv.zip` into the export dir.

- [ ] **Step 1: Extend `_cmd_export` to emit the bulk formats**

In `scrapers/toronto_bids/cli.py`, add the import near the top (beside the other export imports, line ~5):

```python
from toronto_bids.export.tabular_export import write_csv_zip, write_parquet_files
```

In `_cmd_export`, after the `export_schema(...)` line, add:

```python
        write_parquet_files(conn, out_path.parent)
        write_csv_zip(conn, out_path.parent / "bids-csv.zip")
        print(f"Wrote Parquet + CSV bulk exports to {out_path.parent}")
```

- [ ] **Step 2: Extend the nightly `_export` step**

In `_cmd_nightly`, the `_export` inner function (writes `bids.json` + `schema.json`) gains the bulk formats. After the `export_schema(conn, export_dir / "schema.json", generated_at)` line, add:

```python
            write_parquet_files(conn, export_dir)
            write_csv_zip(conn, export_dir / "bids-csv.zip")
```

- [ ] **Step 3: Write the CLI + nightly tests**

Append to `scrapers/tests/test_cli.py`:

```python
def test_export_writes_parquet_and_csv_bundle(monkeypatch, tmp_path):
    import toronto_bids.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bids.sqlite")
    assert main(["export"]) == 0
    export_dir = tmp_path / "export"
    assert (export_dir / "bids-csv.zip").exists()
    assert (export_dir / "solicitation.parquet").exists()
```

In `scrapers/tests/test_nightly.py`, extend the clean-run assertion (`test_a_clean_run_exits_zero_and_writes_the_export`) with:

```python
    assert (tmp_path / "export" / "bids-csv.zip").exists()
    assert (tmp_path / "export" / "solicitation.parquet").exists()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_cli.py tests/test_nightly.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_cli.py scrapers/tests/test_nightly.py
git commit -m "feat(cli): tb export + nightly emit Parquet files and bids-csv.zip (#161)"
```

---

### Task 5: Publish the bulk formats in `publish-data.sh`

**Files:**
- Modify: `deploy/publish-data.sh`

**Interfaces:**
- Consumes: the parquet files + `bids-csv.zip` written by the nightly export (Task 4); `tb manifest` (#168).
- Produces: parquet files + csv zip uploaded to the release; `manifest.json` includes their sizes.

- [ ] **Step 1: Add path variables and a presence check**

In `deploy/publish-data.sh`, after the `MANIFEST=...` line, add:

```bash
CSV_ZIP="$EXPORT_DIR/bids-csv.zip"        # bundled per-table CSVs (#161)
```

After the schema.json presence check (section 4b), add a bulk-export presence check:

```bash
# 4d. The bulk exports (#161) ride the nightly `tb export` too. Require them — a missing bulk
#     artifact is a publish gap. The parquet files are per-table; glob and confirm at least one.
[ -f "$CSV_ZIP" ] || fail "no bids-csv.zip at $CSV_ZIP — did 'tb export' run?"
PARQUET_FILES=("$EXPORT_DIR"/*.parquet)
[ -e "${PARQUET_FILES[0]}" ] || fail "no *.parquet in $EXPORT_DIR — did 'tb export' run?"
```

- [ ] **Step 2: Add the bulk files to the manifest generation and ASSETS**

Change the manifest generation (section 4c) to include the bulk files:

```bash
"$UV" run --project "$SCRAPERS" tb manifest \
  "$JSON" "$GZ" "$SQLITE" "$CSV_ZIP" "${PARQUET_FILES[@]}" --out "$MANIFEST" \
  || fail "could not generate manifest.json"
```

Change the `ASSETS` array to include the bulk files:

```bash
ASSETS=("$JSON" "$GZ" "$SQLITE" "$SCHEMA" "$MANIFEST" "$CSV_ZIP" "${PARQUET_FILES[@]}")
```

(Note: `PARQUET_FILES` is defined in Step 1 before the manifest/ASSETS lines that use it — confirm ordering when editing.)

- [ ] **Step 3: Update the header doc-comment**

In the header comment (the "Uploads bids.json, ..." line), append the bulk formats so the doc stays accurate:

```bash
# Uploads bids.json, bids.json.gz, bids.sqlite, schema.json, manifest.json, bids-csv.zip and the
# per-table *.parquet files to a rolling `latest` release (bulk formats added in #161), giving the
# static
```

(Preserve the rest of the existing sentence.)

- [ ] **Step 4: Dry-run the publish script**

Run:
```bash
cd /home/alex/toronto-bids
rm -rf /tmp/tb-pub161 && mkdir -p /tmp/tb-pub161/export
echo '{"meta":{"generated_at":"2026-07-20T00:00:00Z"}}' > /tmp/tb-pub161/export/bids.json
echo '{"generated_at":"2026-07-20T00:00:00Z","tables":{}}' > /tmp/tb-pub161/export/schema.json
printf 'x' > /tmp/tb-pub161/export/bids-csv.zip
printf 'p' > /tmp/tb-pub161/export/solicitation.parquet
printf 'p' > /tmp/tb-pub161/export/award.parquet
echo 'db' > /tmp/tb-pub161/bids.sqlite
TB_DATA_DIR=/tmp/tb-pub161 TB_PUBLISH_DRY_RUN=1 bash deploy/publish-data.sh 2>&1 | grep -iE "manifest|upload latest" | head
echo "=== manifest ==="; cat /tmp/tb-pub161/export/manifest.json
```
Expected: the `DRY-RUN gh release upload latest ...` line includes `bids-csv.zip`, `solicitation.parquet`, `award.parquet`; `manifest.json` lists all of them with sizes.

- [ ] **Step 5: Verify the missing-bulk failure path**

Run:
```bash
cd /home/alex/toronto-bids
rm -rf /tmp/tb-pub161b && mkdir -p /tmp/tb-pub161b/export
echo '{"meta":{"generated_at":"2026-07-20T00:00:00Z"}}' > /tmp/tb-pub161b/export/bids.json
echo '{"generated_at":"t","tables":{}}' > /tmp/tb-pub161b/export/schema.json
echo 'db' > /tmp/tb-pub161b/bids.sqlite
TB_DATA_DIR=/tmp/tb-pub161b TB_PUBLISH_DRY_RUN=1 bash deploy/publish-data.sh 2>&1 | grep -iE "csv|parquet|fail" | head
```
Expected: fails loudly with "no bids-csv.zip ...".

- [ ] **Step 6: Commit**

```bash
git add deploy/publish-data.sh
git commit -m "feat(deploy): publish Parquet files + bids-csv.zip and size them in the manifest (#161)"
```

---

## Self-Review

**Spec coverage:**
- Per-table Parquet (pyarrow), individual files → Task 3 + Task 5 (uploaded individually). ✅
- `bids-csv.zip` bundle → Task 2. ✅
- Flat SQLite tables via `db.EXPORT_TABLES` → Tasks 2-3. ✅
- Column exclusions (raw_json, text, local_path) → Task 2 `_EXCLUDE_COLUMNS` + name rule, tested in Tasks 2-3. ✅
- Deterministic `ORDER BY 1`, tuple rows → Task 2. ✅
- pyarrow core dep + re-lock → Task 1. ✅
- `tb export` + nightly emit them → Task 4. ✅
- publish-data.sh uploads + manifest sizes + presence check → Task 5. ✅
- `bids.json` untouched → no task modifies `build_export_document`/`export_json`. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `_read_table(conn, table) -> (cols, rows)`, `write_csv_zip(conn, out_path) -> Path`, `write_parquet_files(conn, out_dir) -> list[Path]`, `db.EXPORT_TABLES` — consistent across Tasks 2-5. ✅

**Risk note (Task 3):** Arrow per-column type inference assumes each column's values are type-consistent (the store's affinity discipline). If a live table has genuinely mixed Python types in one column, `pa.table` would raise `ArrowInvalid`. The mandatory live-run verification below is where that would surface; if it does, the fix is a per-column string fallback — do not pre-build it (YAGNI).

**Mandatory live-run verification (after Task 5, before finishing):** against a real synced DB (or the server's `bids.sqlite` if reachable), run `uv run tb export` and confirm: all 18 `*.parquet` files write without an `ArrowInvalid`, `bids-csv.zip` opens, `pq.read_table` on the largest tables (`bid`, `award`, `solicitation`) returns the expected row counts (matching `schema.json`), and the total bulk size is reasonable (blobs excluded). Report the per-file sizes and row counts.
