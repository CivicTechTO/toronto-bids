# schema.json + manifest.json Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish two generated JSON assets in the `toronto-bids-data` release — `schema.json` (a column-level data dictionary from the SQLite, with row counts, observed enums, and curated descriptions) and `manifest.json` (published-artifact file sizes) — so the frontend `/data/` page can render a real dictionary + sizes (#168).

**Architecture:** Two pure builders in a new `export/schema_export.py`, following the export seam's contract (pure over `conn`, deterministic given `generated_at`). `tb export` writes `schema.json` beside `bids.json`; a new `tb manifest` command writes `manifest.json` at publish time from actual bytes; `publish-data.sh` uploads both.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `tomllib`, `json`, `pathlib`, `importlib.resources`), pytest. No new dependencies.

## Global Constraints

- No new third-party dependencies — stdlib only (the spec adds no library).
- Builders are pure and deterministic: no file I/O inside `build_*_document`, every query `ORDER BY`, byte-identical given the same DB + `generated_at` (the `build_export_document` contract in `export/document.py`).
- Row counts live **only** in `schema.json` — one generated source (per #168).
- The table set is the 18 tables `db.counts` enumerates, shared as one constant so the two cannot drift.
- Enums are **observed** (`SELECT DISTINCT`), for a **declared** set of coded `(table, column)` pairs — not auto-detected.
- Descriptions are a curated **high-value subset** in `toronto_bids/data/schema_dictionary.toml`, keyed `"<table>.<column>"`; every key must resolve to a real column (a test enforces this).
- Tests are offline/fixture-based (`uv run pytest`), using the existing in-memory `conn` fixture (`tests/conftest.py`).

---

### Task 1: Shared table constant + schema builder core

**Files:**
- Modify: `scrapers/toronto_bids/store/db.py` (extract the table list in `counts()` to a module constant)
- Create: `scrapers/toronto_bids/export/schema_export.py`
- Test: `scrapers/tests/test_schema_export.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.upsert_row` (existing); models from `toronto_bids.models`.
- Produces:
  - `db.EXPORT_TABLES: list[str]` — the canonical ordered table list.
  - `schema_export.build_schema_document(conn, generated_at: str | None = None) -> dict`.

- [ ] **Step 1: Extract the table list to a constant**

In `scrapers/toronto_bids/store/db.py`, replace the inline list inside `counts()` with a module-level constant and reference it. Add near the top of the file (after `_TABLES`):

```python
# The published table set — the export dictionary (#168) and counts() share this so they
# cannot drift. Ordered for a deterministic schema document.
EXPORT_TABLES = [
    "solicitation", "award", "noncompetitive", "ariba_posting",
    "suspended_firm", "supplier", "capital_project", "bid", "council_item",
    "background_pdf", "composite_award", "sync_run", "buyer",
    "agency_solicitation", "agency_award", "agency_bid", "ariba_attachment",
    "solicitation_link",
]
```

Then change `counts()` to:

```python
def counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in EXPORT_TABLES}
```

- [ ] **Step 2: Write the failing test for the builder core**

Create `scrapers/tests/test_schema_export.py`:

```python
from toronto_bids.export.schema_export import build_schema_document
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db


def test_all_tables_present_and_ordered(conn):
    doc = build_schema_document(conn, generated_at="2026-07-20T00:00:00Z")
    assert doc["generated_at"] == "2026-07-20T00:00:00Z"
    assert list(doc["tables"].keys()) == db.EXPORT_TABLES


def test_column_type_nullable_and_pk(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert cols["document_number"]["type"] == "TEXT"
    assert cols["document_number"]["primary_key"] is True
    # first_seen is declared NOT NULL -> nullable False; status is nullable
    assert cols["first_seen"]["nullable"] is False
    assert cols["status"]["nullable"] is True
    assert "primary_key" not in cols["status"]


def test_row_count_matches(conn):
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Acme",
                              award_amount="1000", source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    assert doc["tables"]["solicitation"]["row_count"] == 1
    assert doc["tables"]["award"]["row_count"] == 1
    assert doc["tables"]["bid"]["row_count"] == 0
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.export.schema_export'`

- [ ] **Step 4: Implement the builder core (no descriptions/enums yet)**

Create `scrapers/toronto_bids/export/schema_export.py`:

```python
from datetime import datetime, timezone

from toronto_bids.store import db


def build_schema_document(conn, generated_at: str | None = None) -> dict:
    """A column-level data dictionary generated from the SQLite (#168).

    Pure and deterministic given `generated_at`: PRAGMA table_info + COUNT(*) per table,
    no file I/O. type/nullable/primary_key come from the declared schema; row_count from
    the live data. Descriptions (curated) and enums (observed) are layered on in later steps.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    tables: dict[str, dict] = {}
    for table in db.EXPORT_TABLES:
        columns = []
        for row in conn.execute(f"PRAGMA table_info({table})"):
            # row: (cid, name, type, notnull, dflt_value, pk)
            col: dict = {"name": row[1], "nullable": not row[3]}
            if row[2]:
                col["type"] = row[2]
            if row[5]:
                col["primary_key"] = True
            columns.append(col)
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tables[table] = {"row_count": row_count, "columns": columns}

    return {"generated_at": generated_at, "tables": tables}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -v`
Expected: PASS (3 tests). Also run `uv run pytest tests/test_export_document.py -q` to confirm the `counts()` refactor didn't regress.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/store/db.py scrapers/toronto_bids/export/schema_export.py scrapers/tests/test_schema_export.py
git commit -m "feat(export): schema dictionary builder core — tables/columns/type/nullable/pk/row_count (#168)"
```

---

### Task 2: Observed enums for declared coded columns

**Files:**
- Modify: `scrapers/toronto_bids/export/schema_export.py`
- Test: `scrapers/tests/test_schema_export.py`

**Interfaces:**
- Consumes: `build_schema_document` (Task 1).
- Produces: a module constant `_ENUM_COLUMNS: set[tuple[str, str]]`; each declared column gains an `enum` key when it has observed values.

- [ ] **Step 1: Write the failing enum tests**

Append to `scrapers/tests/test_schema_export.py`:

```python
from toronto_bids.models import Bid


def test_enum_is_observed_sorted_distinct(conn):
    db.upsert_row(conn, Solicitation(document_number="1000000001", status="Open",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000002", status="Awarded",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000003", status="Awarded",
                                     source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert cols["status"]["enum"] == ["Awarded", "Open"]


def test_enum_omitted_when_no_values(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert "enum" not in cols["status"]  # empty table -> no observed values


def test_non_enum_column_never_gets_enum(conn):
    db.upsert_row(conn, Solicitation(document_number="1000000001", title="Real Title",
                                     source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert "enum" not in cols["title"]  # title is not a declared coded column
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -k enum -v`
Expected: FAIL — `KeyError: 'enum'` / assertion errors (no enum logic yet).

- [ ] **Step 3: Implement observed enums**

In `scrapers/toronto_bids/export/schema_export.py`, add the constant below the imports:

```python
# Coded columns whose observed value set is worth publishing as an enum. Declared (not
# auto-detected by cardinality) so `title` — hundreds of distinct values but not coded —
# is never mistaken for one. Enums are OBSERVED (SELECT DISTINCT), so a new City value
# appears automatically (the drift #168 exists to prevent).
_ENUM_COLUMNS = {
    ("solicitation", "status"),
    ("solicitation", "rfx_type"),
    ("solicitation", "form_type"),
    ("solicitation", "title_source"),
    ("award", "award_amount_verdict"),
    ("award", "source"),
    ("noncompetitive", "contract_amount_verdict"),
    ("bid", "hst_basis"),
    ("bid", "source"),
    ("suspended_firm", "status"),
    ("sync_run", "status"),
    ("background_pdf", "kind"),
    ("agency_award", "value_confidential"),
}


def _observed_enum(conn, table: str, column: str) -> list:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} IS NOT NULL ORDER BY {column}"
    ).fetchall()
    return [r[0] for r in rows]
```

Then in the column loop, after building `col` and before appending, add the enum when declared and non-empty:

```python
            if (table, row[1]) in _ENUM_COLUMNS:
                values = _observed_enum(conn, table, row[1])
                if values:
                    col["enum"] = values
            columns.append(col)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -v`
Expected: PASS (all tests, enum + core).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/schema_export.py scrapers/tests/test_schema_export.py
git commit -m "feat(export): observed enums for declared coded columns in schema.json (#168)"
```

---

### Task 3: Curated descriptions from a TOML dictionary

**Files:**
- Create: `scrapers/toronto_bids/data/schema_dictionary.toml`
- Modify: `scrapers/toronto_bids/export/schema_export.py`
- Test: `scrapers/tests/test_schema_export.py`

**Interfaces:**
- Consumes: `build_schema_document` (Task 1/2); `importlib.resources`, `tomllib`.
- Produces: `schema_export.load_descriptions(text: str | None = None) -> dict[str, str]`; columns gain a `description` where the dictionary has `"<table>.<column>"`.

- [ ] **Step 1: Write the curated TOML (high-value subset)**

Create `scrapers/toronto_bids/data/schema_dictionary.toml`. Keys are `"<table>.<column>"`, each with a single `description`. Mine the glosses from `scrapers/toronto_bids/store/schema.sql` inline comments. Include at minimum these documented-trap and identifier columns (add more from schema.sql as they add value, but keep to the high-value subset — do NOT gloss every `id`/`first_seen`/`last_seen`):

```toml
["solicitation.document_number"]
description = "10-digit normalized competitive identifier — the join key across award/bid/ariba_posting (the primary key)."

["solicitation.title"]
description = "The solicitation title. NULL means the City never published one (≈72% of rows); it does NOT mean 'unknown-and-recoverable'. See title_source for recovered titles."

["solicitation.title_source"]
description = "Where a recovered title came from when the City published none: 'bid_award_panel' | 'legacy_ariba_html' | 'council_pre_ariba'. NULL = the City's own feed."

["solicitation.source"]
description = "Which source last wrote the ROW (the OData spine owns this and re-upserts every sync). NOT title provenance — that is title_source."

["award.document_number"]
description = "The solicitation this award line belongs to (joins solicitation.document_number)."

["award.award_amount"]
description = "The City's published amount string, verbatim ('$1,317,169.92 CAD', 'kj'). Archive fidelity — NOT summable. Aggregate award_amount_numeric instead."

["award.award_amount_numeric"]
description = "Machine-parsed CAD amount (toronto_bids/amount.py). NULL beside a non-NULL award_amount means the raw string is not a single CAD amount. Aggregate on THIS column."

["award.award_amount_labelled"]
description = "Human-adjudicated amount for a string the parser refused (#74). Never merged into award_amount_numeric — keep that column machine-only."

["award.award_amount_verdict"]
description = "The human verdict behind award_amount_labelled: amount | not_an_amount | corrupt | unknown | not_an_award."

["award.source"]
description = "Award provenance ('odata' spine or 'ckan_awarded' cross-check). The same (document, supplier) can appear once per source — filter source='odata' or GROUP BY to de-duplicate."

["noncompetitive.workspace_number"]
description = "Identifier for a non-competitive contract — a separate keyspace with no join to document_number."

["noncompetitive.contract_amount"]
description = "The City's published contract-amount string, verbatim. NOT summable — aggregate contract_amount_numeric."

["noncompetitive.contract_amount_numeric"]
description = "Machine-parsed CAD contract amount. NULL when the raw string is not a single CAD amount."

["bid.reference"]
description = "The council item (e.g. '2016.BD106.3') this bid was tabulated under, for Bid Award Panel-era bids. NULL for Award Summary Form bids, which carry a document_number instead."

["bid.document_number"]
description = "The solicitation this bid belongs to (Award Summary Form era). NULL for panel-era bids, which carry a council reference instead."

["bid.bid_price"]
description = "The bidder's price, verbatim — including outcome strings the City writes in the price column ('Non-Compliant', 'No bid'). NOT the award value (a bid excludes contingency). Aggregate bid_price_numeric."

["bid.bid_price_numeric"]
description = "Machine-parsed bid price; NULL for the non-numeric outcome strings above."

["bid.hst_basis"]
description = "The tax basis a bid_price is stated on. Load-bearing: comparing prices across different bases without it is wrong."

["composite_award.call_number"]
description = "2009-2012 Call Number — a third keyspace predating Ariba, joining to neither document_number nor workspace_number (#96)."

["composite_award.award_value_numeric"]
description = "The FIRST net-of-taxes figure (initial term, excluding option years). Option-year/'total potential' figures beside it can be 2× larger and are deliberately not used."

["buyer.partnered"]
description = "Whether this agency buyer is only partly City-funded (e.g. TRCA). Set so a consumer can segment agency records rather than mix them into City-only headline counts (#135)."

["buyer.funding_share"]
description = "The City's funding share of a partnered buyer (TRCA: 0.626), for the segmentation above."

["agency_award.value_confidential"]
description = "1 when the award value was routed to a confidential attachment (MFIPPA) rather than published — a real distinction, not a missing NULL (#135)."

["solicitation_link.reference"]
description = "A pre-Ariba council item matched to a spine solicitation by (winner, value) — the #124/#165 equivalence, consumed by the export to nest pre-Ariba bids under their solicitation."

["solicitation_link.document_number"]
description = "The spine solicitation that solicitation_link.reference is the same procurement as."
```

- [ ] **Step 2: Write the failing description + integrity tests**

Append to `scrapers/tests/test_schema_export.py`:

```python
from toronto_bids.export.schema_export import load_descriptions


def test_description_applied_where_present(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["award"]["columns"]}
    assert "verbatim" in cols["award_amount"]["description"]
    assert "description" not in cols["first_seen"]  # no gloss for bookkeeping columns


def test_every_dictionary_key_resolves_to_a_real_column(conn):
    # A stale key (renamed/removed column) must surface loudly.
    real = set()
    for table in db.EXPORT_TABLES:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            real.add(f"{table}.{row[1]}")
    for key in load_descriptions():
        assert key in real, f"schema_dictionary.toml key {key!r} is not a real column"
```

- [ ] **Step 3: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -k "description or resolves" -v`
Expected: FAIL — `ImportError: cannot import name 'load_descriptions'`.

- [ ] **Step 4: Implement description loading + application**

In `scrapers/toronto_bids/export/schema_export.py`, add imports and loader:

```python
import tomllib
from importlib import resources
```

```python
def load_descriptions(text: str | None = None) -> dict[str, str]:
    """The curated column dictionary, keyed '<table>.<column>' -> gloss (#168)."""
    if text is None:
        text = resources.files("toronto_bids.data").joinpath("schema_dictionary.toml").read_text()
    return {key: entry["description"] for key, entry in tomllib.loads(text).items()}
```

In `build_schema_document`, load the dictionary once at the top (after `generated_at`):

```python
    descriptions = load_descriptions()
```

and in the column loop, after the enum block and before `columns.append(col)`:

```python
            gloss = descriptions.get(f"{table}.{row[1]}")
            if gloss:
                col["description"] = gloss
```

- [ ] **Step 5: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/data/schema_dictionary.toml scrapers/toronto_bids/export/schema_export.py scrapers/tests/test_schema_export.py
git commit -m "feat(export): curated column descriptions for schema.json high-value subset (#168)"
```

---

### Task 4: Manifest builder + `export_schema` serializer

**Files:**
- Modify: `scrapers/toronto_bids/export/schema_export.py`
- Modify: `scrapers/toronto_bids/export/json_export.py`
- Test: `scrapers/tests/test_schema_export.py`, `scrapers/tests/test_json_export.py`

**Interfaces:**
- Consumes: `build_schema_document` (Tasks 1-3).
- Produces:
  - `schema_export.build_manifest_document(files, generated_at: str | None = None) -> dict` where `files` is an iterable of path-likes.
  - `json_export.export_schema(conn, out_path, generated_at=None) -> Path`.

- [ ] **Step 1: Write the failing manifest + serializer tests**

Append to `scrapers/tests/test_schema_export.py`:

```python
from pathlib import Path

from toronto_bids.export.schema_export import build_manifest_document


def test_manifest_sizes_and_order(tmp_path):
    a = tmp_path / "bids.json"; a.write_bytes(b"x" * 10)
    b = tmp_path / "bids.json.gz"; b.write_bytes(b"y" * 3)
    doc = build_manifest_document([a, b], generated_at="t")
    assert doc["generated_at"] == "t"
    assert doc["artifacts"] == [
        {"name": "bids.json", "bytes": 10},
        {"name": "bids.json.gz", "bytes": 3},
    ]


def test_manifest_omits_missing_file(tmp_path):
    a = tmp_path / "bids.json"; a.write_bytes(b"x" * 5)
    missing = tmp_path / "nope.sqlite"
    doc = build_manifest_document([a, missing], generated_at="t")
    assert [x["name"] for x in doc["artifacts"]] == ["bids.json"]
```

Append to `scrapers/tests/test_json_export.py`:

```python
import json

from toronto_bids.export.json_export import export_schema


def test_export_schema_writes_valid_json(conn, tmp_path):
    out = tmp_path / "schema.json"
    written = export_schema(conn, out, generated_at="2026-07-20T00:00:00Z")
    assert written == out
    doc = json.loads(out.read_text())
    assert doc["generated_at"] == "2026-07-20T00:00:00Z"
    assert "solicitation" in doc["tables"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py -k manifest tests/test_json_export.py -k export_schema -v`
Expected: FAIL — import errors (`build_manifest_document`, `export_schema` undefined).

- [ ] **Step 3: Implement the manifest builder**

Add to `scrapers/toronto_bids/export/schema_export.py`:

```python
from pathlib import Path


def build_manifest_document(files, generated_at: str | None = None) -> dict:
    """Published-artifact file sizes (#168). Pure over the given paths; a file that does not
    exist is omitted (publish only passes files it has already written/verified)."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    artifacts = []
    for f in files:
        p = Path(f)
        if p.exists():
            artifacts.append({"name": p.name, "bytes": p.stat().st_size})
    return {"generated_at": generated_at, "artifacts": artifacts}
```

- [ ] **Step 4: Implement `export_schema`**

Add to `scrapers/toronto_bids/export/json_export.py`:

```python
from toronto_bids.export.schema_export import build_schema_document


def export_schema(conn, out_path, generated_at: str | None = None) -> Path:
    document = build_schema_document(conn, generated_at)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
```

- [ ] **Step 5: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_schema_export.py tests/test_json_export.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/export/schema_export.py scrapers/toronto_bids/export/json_export.py scrapers/tests/test_schema_export.py scrapers/tests/test_json_export.py
git commit -m "feat(export): manifest builder + export_schema serializer (#168)"
```

---

### Task 5: Wire `tb export` (schema.json) and `tb manifest` CLI

**Files:**
- Modify: `scrapers/toronto_bids/cli.py`
- Test: `scrapers/tests/test_cli.py` (or create if absent — check first)

**Interfaces:**
- Consumes: `export_json`, `export_schema` (Task 4), `build_manifest_document` (Task 4).
- Produces: `tb export` also writes `schema.json`; new subcommand `tb manifest <file…> --out <path>`.

- [ ] **Step 1: Extend `tb export` to also write schema.json**

In `scrapers/toronto_bids/cli.py`, update the import:

```python
from toronto_bids.export.json_export import export_json, export_schema
```

Modify `_cmd_export` (currently lines ~206-217) so it writes `schema.json` beside `bids.json` with a shared `generated_at`:

```python
def _cmd_export(args) -> int:
    from pathlib import Path
    from datetime import datetime, timezone

    conn = _open_db()
    try:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "export" / "bids.json"
        generated_at = datetime.now(timezone.utc).isoformat()
        written = export_json(conn, out_path, generated_at)
        schema_path = out_path.parent / "schema.json"
        export_schema(conn, schema_path, generated_at)
        counts = db.counts(conn)
        print(f"Exported {counts['solicitation']} solicitations to {written}")
        print(f"Wrote schema dictionary to {schema_path}")
    finally:
        conn.close()
    return 0
```

- [ ] **Step 2: Add the `tb manifest` subcommand parser**

In `build_parser`, after the `p_export` block (~line 21), add:

```python
    p_manifest = sub.add_parser(
        "manifest",
        help="Write manifest.json (published-artifact file sizes) for the given files (#168)")
    p_manifest.add_argument("files", nargs="+", help="Artifact files to size")
    p_manifest.add_argument("--out", required=True, help="Output path for manifest.json")
```

- [ ] **Step 3: Add the `_cmd_manifest` handler**

Add near `_cmd_export`:

```python
def _cmd_manifest(args) -> int:
    import json
    from pathlib import Path

    from toronto_bids.export.schema_export import build_manifest_document

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document = build_manifest_document(args.files)
    out_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"Wrote manifest ({len(document['artifacts'])} artifacts) to {out_path}")
    return 0
```

- [ ] **Step 4: Dispatch it in `main`**

In `main` (after the `export` dispatch, ~line 791):

```python
    if args.command == "manifest":
        return _cmd_manifest(args)
```

- [ ] **Step 5: Write a CLI test**

First check for an existing CLI test file: `ls scrapers/tests/ | grep cli`. If `test_cli.py` exists, append; otherwise create `scrapers/tests/test_cli.py`:

```python
import json

from toronto_bids.cli import main


def test_manifest_command_writes_sizes(tmp_path):
    a = tmp_path / "bids.json"; a.write_bytes(b"x" * 12)
    out = tmp_path / "manifest.json"
    rc = main(["manifest", str(a), "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["artifacts"] == [{"name": "bids.json", "bytes": 12}]
```

- [ ] **Step 6: Run to verify pass**

Run: `cd scrapers && uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 7: Verify `tb export` writes both files against a real (or seeded) DB**

Run:
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-schema-check uv run python -c "
from toronto_bids.store import db
from toronto_bids.models import Solicitation
from pathlib import Path
import toronto_bids.config as config
conn = db.connect(':memory:'); db.init_db(conn)
db.upsert_row(conn, Solicitation(document_number='1000000001', status='Open', source='odata'), overwrite=True); conn.commit()
from toronto_bids.export.json_export import export_schema
p = export_schema(conn, Path('/tmp/tb-schema-check/schema.json'), generated_at='t')
import json; d = json.loads(p.read_text())
print('tables:', len(d['tables']), '| solicitation cols:', len(d['tables']['solicitation']['columns']))
print('status enum:', [c.get('enum') for c in d['tables']['solicitation']['columns'] if c['name']=='status'])
"
```
Expected: prints 18 tables and a `status` enum of `['Open']`.

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_cli.py
git commit -m "feat(cli): tb export writes schema.json; new tb manifest command (#168)"
```

---

### Task 6: Publish both assets in `publish-data.sh`

**Files:**
- Modify: `deploy/publish-data.sh`

**Interfaces:**
- Consumes: `tb manifest` (Task 5); `schema.json` written by the nightly `tb export` (Task 5).
- Produces: `schema.json` + `manifest.json` uploaded to the `latest` and monthly-snapshot releases.

- [ ] **Step 1: Add asset paths and generate manifest.json after gzip**

In `deploy/publish-data.sh`, add path variables near the top (after `SQLITE=...`):

```bash
SCHEMA="$EXPORT_DIR/schema.json"
MANIFEST="$EXPORT_DIR/manifest.json"
```

After the gzip step (section 4, `gzip -9 -c "$JSON" > "$GZ" || fail "gzip failed"`), add manifest generation and a schema.json presence check:

```bash
# 4b. schema.json is written beside bids.json by the nightly `tb export` (#168). Require it —
#     a missing dictionary is a publish gap the frontend /data/ page depends on.
[ -f "$SCHEMA" ] || fail "no schema.json at $SCHEMA — did 'tb export' run?"

# 4c. Generate manifest.json from the ACTUAL bytes about to be uploaded (sizes can't drift).
uv run --project scrapers tb manifest "$JSON" "$GZ" "$SQLITE" --out "$MANIFEST" \
  || fail "could not generate manifest.json"
```

- [ ] **Step 2: Add both to the ASSETS array**

Change the `ASSETS` definition (section 4/5) from:

```bash
ASSETS=("$JSON" "$GZ" "$SQLITE")
```

to:

```bash
ASSETS=("$JSON" "$GZ" "$SQLITE" "$SCHEMA" "$MANIFEST")
```

(Both the `latest` upload and the monthly snapshot use `ASSETS`, so this covers both.)

- [ ] **Step 3: Dry-run the publish script to confirm the new steps**

Run:
```bash
cd /home/alex/toronto-bids
mkdir -p /tmp/tb-pub/export
echo '{"meta":{"generated_at":"2026-07-20T00:00:00Z"}}' > /tmp/tb-pub/export/bids.json
echo '{"generated_at":"2026-07-20T00:00:00Z","tables":{}}' > /tmp/tb-pub/export/schema.json
echo 'db' > /tmp/tb-pub/bids.sqlite
TB_DATA_DIR=/tmp/tb-pub TB_PUBLISH_DRY_RUN=1 bash deploy/publish-data.sh
```
Expected: the output shows `manifest.json` generated (a real `tb manifest` run, not dry-run), then `DRY-RUN gh release upload latest …` lines that include both `schema.json` and `manifest.json`. Confirm `/tmp/tb-pub/export/manifest.json` exists and lists `bids.json`, `bids.sqlite` (gz is created by the script).

- [ ] **Step 4: Commit**

```bash
git add deploy/publish-data.sh
git commit -m "feat(deploy): publish schema.json + manifest.json to the data release (#168)"
```

---

## Self-Review

**Spec coverage:**
- schema.json column dictionary (type/nullable/pk) → Task 1. ✅
- Observed enums for coded columns → Task 2. ✅
- Curated descriptions (high-value subset) + integrity test → Task 3. ✅
- Per-table row counts (schema.json only) → Task 1. ✅
- manifest.json file sizes → Task 4 (builder) + Task 6 (publish-time generation). ✅
- `tb export` emits schema.json; `tb manifest` command → Task 5. ✅
- publish-data.sh uploads both → Task 6. ✅
- Determinism (ORDER BY, shared generated_at) → Task 1 builder + Task 5 shared timestamp. ✅
- Shared table constant (no drift with counts) → Task 1. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `build_schema_document(conn, generated_at)`, `build_manifest_document(files, generated_at)`, `export_schema(conn, out_path, generated_at)`, `load_descriptions(text=None)`, `db.EXPORT_TABLES` — names/signatures consistent across Tasks 1-6. ✅

**Note for the enum column set (Task 2):** the declared `_ENUM_COLUMNS` list is my best read of the coded columns; the implementer should verify each named column exists in `schema.sql` (e.g. confirm `background_pdf.kind`, `agency_award.value_confidential`, `suspended_firm.status` are the real column names) and drop any that aren't — a non-existent column would make `PRAGMA`-driven code never match (harmless) but a wrong table/column in `_observed_enum` would raise at runtime. Verify before committing Task 2.
