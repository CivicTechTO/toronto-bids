# Per-solicitation document index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recursively index the contents of captured Ariba attachment bundles and surface a per-solicitation `documents` listing (Ariba files + the 229 orphaned Award Summary Forms) in the public export.

**Architecture:** Extend the `ariba_attachment` index with a `path` column and make `index_zip` descend nested zips to any depth (guarded). Rebuild the index from the on-disk bytes (a derived layer, like the supplier dimension). Add a `documents` array to the export builder that unions the two `document_number`-keyed corpora.

**Tech Stack:** Python 3.12+, `uv`, stdlib `zipfile`/`io`, SQLite, pytest.

## Global Constraints

- Python **3.12+**, `uv`-managed. Install/run via `uv run`. No lint/format/typecheck configured — do not invent those commands.
- Tests are **offline and fixture-based** — no network, no browser. CI runs `uv sync --locked && uv run pytest`.
- The export builder (`build_export_document`) is **pure and deterministic**: every query `ORDER BY`, no file I/O.
- Rows are never deleted, **except derived indexes**: `ariba_attachment` is a derived index of the on-disk zips and is sanctioned to rebuild-from-bytes (same as `linking/supplier.py:build_supplier_dimension`).
- Recursion caps: **max depth 8, max total entries per bundle 10000** (zip-bomb guard).
- Work on branch `feat-123-document-index`. Run all commands from `scrapers/`.

---

### Task 1: Recursive `index_zip`

Make `index_zip` descend nested zips, returning one dict per **leaf** file with a full nested `path`. This is the pure core; no DB, no schema.

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py` (`index_zip`, lines 64-81; add `import io` at top)
- Test: `scrapers/tests/test_ariba_attachments.py`

**Interfaces:**
- Produces: `index_zip(zip_path) -> list[dict]` where each dict is `{"filename": str (leaf), "path": str (full nested), "file_size": int, "crc32": str}`. A nested zip that is expandable contributes its leaves (not itself); one that is empty, corrupt, encrypted, or past the depth/entry cap is recorded as a single leaf (its own path, `type` will read as `zip` downstream).

- [ ] **Step 1: Write the failing tests**

Add to `scrapers/tests/test_ariba_attachments.py` (the `_make_zip` helper already exists):

```python
import io


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_index_zip_recurses_into_nested_zips_with_full_paths(tmp_path):
    inner = _zip_bytes({"drawings/site-plan.pdf": b"plan", "notes.txt": b"n"})
    z = _make_zip(tmp_path / "Doc1.zip", {
        "PART 1 - RFP.pdf": b"rfp",
        "Appendix C2 - Planning Documents.zip": inner,
    })
    by_path = {e["path"]: e for e in aa.index_zip(z)}
    # The container zip is gone; its contents surface with prefixed paths.
    assert set(by_path) == {
        "PART 1 - RFP.pdf",
        "Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf",
        "Appendix C2 - Planning Documents.zip/notes.txt",
    }
    nested = by_path["Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf"]
    assert nested["filename"] == "drawings/site-plan.pdf"   # leaf name within its zip
    assert nested["file_size"] == len(b"plan")
    assert int(nested["crc32"], 16) == zipfile.crc32(b"plan")


def test_index_zip_records_a_corrupt_nested_zip_as_a_leaf(tmp_path):
    z = _make_zip(tmp_path / "Doc2.zip", {
        "good.pdf": b"ok",
        "broken.zip": b"not a valid zip file",
    })
    by_path = {e["path"]: e for e in aa.index_zip(z)}
    # The unreadable zip is kept as its own leaf rather than lost or fatal.
    assert set(by_path) == {"good.pdf", "broken.zip"}


def test_index_zip_caps_total_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "_MAX_ZIP_ENTRIES", 3)
    z = _make_zip(tmp_path / "Doc3.zip", {f"f{i}.pdf": b"x" for i in range(10)})
    assert len(aa.index_zip(z)) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ariba_attachments.py -k "recurses or corrupt or caps" -v`
Expected: FAIL (`index_zip` returns no `path` key / `_MAX_ZIP_ENTRIES` undefined).

- [ ] **Step 3: Implement recursive `index_zip`**

Add `import io` alongside the existing imports at the top of `ariba_attachments.py`. Replace the `index_zip` function (lines 64-81) with:

```python
_MAX_ZIP_DEPTH = 8
_MAX_ZIP_ENTRIES = 10000


def index_zip(zip_path) -> list[dict]:
    """Recursive central-directory listing of a bundle: one dict per LEAF file.

    Nested zips are descended to any depth (a bundle's real documents often live inside
    "Appendix ….zip"), each leaf carrying the full nested `path`. Sizes and CRC32 come from
    each level's central directory; a nested zip must be read (inflated) to reach its own
    directory, so depth and a per-bundle entry budget bound zip bombs. A nested zip that is
    empty, corrupt, encrypted, or past a cap is kept as a single leaf rather than lost.
    """
    with zipfile.ZipFile(zip_path) as zf:
        return _index_zipfile(zf, prefix="", depth=0, budget=[_MAX_ZIP_ENTRIES])


def _index_zipfile(zf, prefix: str, depth: int, budget: list) -> list[dict]:
    out = []
    for zi in zf.infolist():
        if zi.is_dir() or budget[0] <= 0:
            continue
        path = prefix + zi.filename
        if zi.filename.lower().endswith(".zip") and depth < _MAX_ZIP_DEPTH:
            try:
                with zipfile.ZipFile(io.BytesIO(zf.read(zi.filename))) as nested:
                    children = _index_zipfile(nested, path + "/", depth + 1, budget)
                if children:                       # expandable: contribute its leaves, not it
                    out.extend(children)
                    continue
                # empty zip falls through and is kept as a leaf, so nothing is silently dropped
            except (zipfile.BadZipFile, RuntimeError, OSError):
                pass                               # corrupt/encrypted: keep as an opaque leaf
        out.append({
            "filename": zi.filename,
            "path": path,
            "file_size": zi.file_size,
            "crc32": format(zi.CRC & 0xFFFFFFFF, "08x"),
        })
        budget[0] -= 1
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ariba_attachments.py -v`
Expected: PASS (new tests plus the existing `index_zip`/`store_bundle`/`ingest` tests — note the existing `test_index_zip_lists_files_with_size_and_crc_and_drops_directories` still passes because non-zip files are unaffected).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py scrapers/tests/test_ariba_attachments.py
git commit -m "feat(ariba): recurse into nested zips when indexing bundles (#123)"
```

---

### Task 2: `path` column, model, and schema migration

Add `path` to the schema, model, and upsert key, and migrate existing databases (which have `UNIQUE (document_number, filename)`) to `UNIQUE (document_number, path)`.

**Files:**
- Modify: `scrapers/toronto_bids/store/schema.sql` (the `ariba_attachment` CREATE TABLE)
- Modify: `scrapers/toronto_bids/models.py` (`AribaAttachment` dataclass)
- Modify: `scrapers/toronto_bids/store/db.py` (`_TABLES` entry line 16; add `_rebuild_ariba_attachment_for_path`; call it in `init_db`)
- Test: `scrapers/tests/test_ariba_attachments.py`

**Interfaces:**
- Produces: `AribaAttachment(document_number, filename, path=None, file_size=None, crc32=None, zip_name=None, zip_sha256=None)`. Upsert key is `(document_number, path)`. `init_db` migrates a pre-`path` database in place.

- [ ] **Step 1: Write the failing test**

Add to `scrapers/tests/test_ariba_attachments.py`:

```python
from toronto_bids.store import db as _db
from toronto_bids.models import AribaAttachment


def test_init_db_migrates_old_unique_index_to_path(tmp_path):
    # Build a database with the OLD schema (UNIQUE on filename) and a stale row.
    conn = _db.connect(":memory:")
    conn.executescript(
        "CREATE TABLE ariba_attachment (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_number TEXT NOT NULL, filename TEXT NOT NULL, file_size INTEGER, "
        "crc32 TEXT, zip_name TEXT, zip_sha256 TEXT, "
        "first_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "last_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "UNIQUE (document_number, filename));")
    conn.execute("INSERT INTO ariba_attachment (document_number, filename) VALUES ('1','a.zip')")
    conn.commit()

    _db.init_db(conn)   # must add `path` and swap the unique index without error

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ariba_attachment)")]
    assert "path" in cols
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='ariba_attachment'").fetchone()[0]
    assert "document_number, path" in sql
    assert "document_number, filename" not in sql
    # Two leaves sharing a filename but differing in path now coexist (the old key forbade it).
    for p in ("x.zip/a.pdf", "y.zip/a.pdf"):
        _db.upsert_row(conn, AribaAttachment(document_number="1", filename="a.pdf", path=p),
                       overwrite=True)
    assert conn.execute("SELECT COUNT(*) FROM ariba_attachment WHERE document_number='1' "
                        "AND filename='a.pdf'").fetchone()[0] == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ariba_attachments.py::test_init_db_migrates_old_unique_index_to_path -v`
Expected: FAIL (`path` column missing / old unique index unchanged).

- [ ] **Step 3a: Update the schema**

In `scrapers/toronto_bids/store/schema.sql`, change the `ariba_attachment` table so it has a `path` column and the new unique key. Replace the `filename` line region and the `UNIQUE` line:

```sql
    document_number TEXT NOT NULL,   -- the Ariba event; joins solicitation.document_number
    filename        TEXT NOT NULL,   -- the LEAF name of one file inside the bundle
    -- Full nested path within the bundle, e.g. 'Appendix C2.zip/drawings/site.pdf'. The real
    -- identity: leaf names collide across nested zips. Recursively expanded (#123).
    path            TEXT,
    file_size       INTEGER,         -- uncompressed bytes, from the zip central directory
```

and

```sql
    UNIQUE (document_number, path)
```

- [ ] **Step 3b: Update the model**

In `scrapers/toronto_bids/models.py`, add `path` to `AribaAttachment` (right after `filename`):

```python
    document_number: str
    filename: str
    path: str | None = None
    file_size: int | None = None
    crc32: str | None = None
    zip_name: str | None = None
    zip_sha256: str | None = None
```

- [ ] **Step 3c: Update the upsert key and add the migration**

In `scrapers/toronto_bids/store/db.py`, change the `_TABLES` entry (line 16):

```python
    AribaAttachment: ("ariba_attachment", ["document_number", "path"]),
```

Add this function next to the other `_rebuild_*` helpers:

```python
def _rebuild_ariba_attachment_for_path(conn, schema: str) -> bool:
    """Swap ariba_attachment's UNIQUE(document_number, filename) for UNIQUE(document_number, path).

    Recursive indexing (#123) surfaces leaves that share a filename across different nested zips,
    which the old key rejected. `_add_missing_columns` adds `path` but cannot change a table-level
    UNIQUE, so a database built before #123 needs a genuine rebuild — same pattern as
    _rebuild_bid_for_nullable_reference. Rows are copied so first_seen survives; their `path` is
    NULL until a --reindex rebuilds them from the bytes.

    Returns True if a rebuild happened. Idempotent: a no-op once the key is on path.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ariba_attachment'").fetchone()
    if row is None or "document_number, filename)" not in (row["sql"] or ""):
        return False                        # fresh DB, or already rebuilt

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ariba_attachment)")]
    quoted = ", ".join(cols)
    conn.executescript("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("ALTER TABLE ariba_attachment RENAME TO _ariba_attachment_pre123")
        conn.executescript(schema)
        conn.execute(f"INSERT INTO ariba_attachment ({quoted}) "
                     f"SELECT {quoted} FROM _ariba_attachment_pre123")
        conn.execute("DROP TABLE _ariba_attachment_pre123")
    finally:
        conn.executescript("PRAGMA foreign_keys = ON;")
    return True
```

Call it in `init_db`, after the existing rebuilds (around line 52):

```python
    _rebuild_bid_for_nullable_reference(conn, schema)
    _rebuild_ariba_attachment_for_path(conn, schema)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ariba_attachments.py -v`
Expected: PASS. Then the full suite to confirm no schema regression: `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/store/schema.sql scrapers/toronto_bids/models.py scrapers/toronto_bids/store/db.py scrapers/tests/test_ariba_attachments.py
git commit -m "feat(ariba): add path column + migrate unique index for recursive index (#123)"
```

---

### Task 3: `store_bundle` rebuild-from-bytes + `reindex_bundles`

Make `store_bundle` write the recursive rows (with `path`) and rebuild a document's index from scratch, and add a `reindex_bundles` pass over the on-disk store.

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py` (`store_bundle`, lines 92-119; add `reindex_bundles`)
- Test: `scrapers/tests/test_ariba_attachments.py`

**Interfaces:**
- Consumes: `index_zip` (Task 1), `AribaAttachment` with `path` (Task 2).
- Produces: `store_bundle(conn, zip_path, document_number, dest_dir=None) -> int` now writes `path` on every row and deletes the document's prior rows first. `reindex_bundles(conn, dest_dir=None, log=...) -> int` rebuilds every `Doc*.zip` under the store.

- [ ] **Step 1: Write the failing tests**

Add to `scrapers/tests/test_ariba_attachments.py`:

```python
def test_store_bundle_writes_paths_and_rebuilds_stale_rows(conn, tmp_path):
    inner = _zip_bytes({"a.pdf": b"a"})
    src = _make_zip(tmp_path / "Doc9.zip", {"P1.pdf": b"p", "Appx.zip": inner})
    dest = tmp_path / "store"

    # Simulate a pre-recursion row (the container zip indexed as a top-level leaf).
    aa.store_bundle(conn, src, "9", dest)  # first pass already recursive; force a stale row:
    conn.execute("INSERT INTO ariba_attachment (document_number, filename, path) "
                 "VALUES ('9', 'Appx.zip', 'Appx.zip')")
    conn.commit()

    aa.store_bundle(conn, src, "9", dest)  # rebuild clears the stale container row
    paths = {r["path"] for r in conn.execute(
        "SELECT path FROM ariba_attachment WHERE document_number='9'")}
    assert paths == {"P1.pdf", "Appx.zip/a.pdf"}          # stale 'Appx.zip' leaf gone


def test_reindex_bundles_rebuilds_every_zip_on_disk(conn, tmp_path):
    dest = tmp_path / "store"
    dest.mkdir()
    _make_zip(dest / "Doc11.zip", {"only.pdf": b"x"})
    _make_zip(dest / "Doc12.zip", {"a.pdf": b"a", "b.pdf": b"b"})

    assert aa.reindex_bundles(conn, dest) == 2
    assert conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ariba_attachments.py -k "rebuilds" -v`
Expected: FAIL (`reindex_bundles` undefined; stale row survives because `store_bundle` upserts rather than rebuilds).

- [ ] **Step 3: Implement**

Replace the body of `store_bundle` (keep the copy-to-canonical and sha lines) so it deletes the document's rows then inserts the recursive set. Replace lines 107-119:

```python
    zip_sha = sha256_of_file(canonical)
    entries = index_zip(canonical)
    # Rebuild this document's index from the bytes: ariba_attachment is a derived index of the
    # on-disk zips (like the supplier dimension), so clear the document's rows — dropping any
    # stale top-level-only rows from before recursion — then insert the current recursive set.
    conn.execute("DELETE FROM ariba_attachment WHERE document_number = ?", (document_number,))
    for entry in entries:
        db.upsert_row(conn, AribaAttachment(
            document_number=document_number,
            filename=entry["filename"],
            path=entry["path"],
            file_size=entry["file_size"],
            crc32=entry["crc32"],
            zip_name=canonical.name,
            zip_sha256=zip_sha,
        ), overwrite=True)
    conn.commit()
    return len(entries)
```

Add `reindex_bundles` after `ingest_downloads`:

```python
def reindex_bundles(conn, dest_dir=None, log=lambda _m: None) -> int:
    """Rebuild the index from the zips already under the attachment store. Offline, no browser.

    ariba_attachment is a derived index of the bundles on disk, so it can be regenerated whenever
    the indexing changes (e.g. #123's recursion). Idempotent: each bundle is rebuilt from its
    bytes. Returns the number of bundles reindexed.
    """
    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    if not dest_dir.is_dir():
        return 0
    n = 0
    for zip_path in sorted(dest_dir.glob("Doc*.zip")):
        document_number = document_number_from_zip_name(zip_path.name)
        if document_number is None:
            log(f"  skipped {zip_path.name}: no Doc########## in the name")
            continue
        files = store_bundle(conn, zip_path, document_number, dest_dir)
        log(f"  {zip_path.name}: {files} files")
        n += 1
    return n
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ariba_attachments.py -v`
Expected: PASS (including the existing `store_bundle`/`ingest` tests — they index non-zip files, which now carry `path == filename`).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py scrapers/tests/test_ariba_attachments.py
git commit -m "feat(ariba): store_bundle rebuilds recursively + add reindex_bundles (#123)"
```

---

### Task 4: `--reindex` CLI flag

Expose `reindex_bundles` as `tb enrich-ariba-attachments --reindex`.

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (the `p_ariba` subparser and `_cmd_enrich_ariba_attachments`)
- Test: `scrapers/tests/test_ariba_attachments.py`

**Interfaces:**
- Consumes: `reindex_bundles` (Task 3).
- Produces: `tb enrich-ariba-attachments --reindex` rebuilds the index from `<DATA_DIR>/ariba/attachments/`.

- [ ] **Step 1: Write the failing test**

Add to `scrapers/tests/test_ariba_attachments.py`:

```python
def test_cli_reindex_rebuilds_from_the_store(tmp_path, monkeypatch, capsys):
    from toronto_bids import config, cli
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bids.sqlite")
    store = tmp_path / "ariba" / "attachments"
    store.mkdir(parents=True)
    monkeypatch.setattr(config, "ARIBA_ATTACHMENTS_DIR", store)
    _make_zip(store / "Doc21.zip", {"a.pdf": b"a"})

    assert cli.main(["enrich-ariba-attachments", "--reindex"]) == 0
    out = capsys.readouterr().out
    assert "Doc21.zip" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ariba_attachments.py::test_cli_reindex_rebuilds_from_the_store -v`
Expected: FAIL (`unrecognized arguments: --reindex`).

- [ ] **Step 3: Add the flag and handler branch**

In `scrapers/toronto_bids/cli.py`, add to the `p_ariba` subparser (after the `--headless` argument):

```python
    p_ariba.add_argument(
        "--reindex", action="store_true",
        help="Rebuild the index from the bundles already on disk under <DATA_DIR>/ariba/"
             "attachments/ (offline, no browser). Needed once after the recursion change (#123).")
```

In `_cmd_enrich_ariba_attachments`, add a branch before the `elif args.capture:` branch:

```python
        if args.reindex:
            print("Reindexing bundles on disk (recursive):")
            print(f"  bundles reindexed: {aa.reindex_bundles(conn, log=out)}")
        elif args.ingest:
```

(Change the existing `if args.ingest:` to `elif args.ingest:` so it chains after the new branch.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ariba_attachments.py -v` then `uv run tb enrich-ariba-attachments --help` (visually confirm `--reindex` is listed).
Expected: PASS; help shows `--reindex`.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_ariba_attachments.py
git commit -m "feat(ariba): tb enrich-ariba-attachments --reindex (#123)"
```

---

### Task 5: Per-solicitation `documents` array in the export

Add a `documents` list under each solicitation, unioning Ariba attachment leaves and the Award Summary Forms.

**Files:**
- Modify: `scrapers/toronto_bids/export/document.py` (add an `_ext` helper; build `documents_by_doc`; set `sol["documents"]` in the solicitation loop, lines 59-65)
- Test: `scrapers/tests/test_export_document.py`

**Interfaces:**
- Consumes: `ariba_attachment` rows with `path` (Task 2), `background_pdf` where `kind='award_summary'`.
- Produces: each solicitation dict gains `"documents": list` of `{source, name, path, type, size_bytes, url}`.

- [ ] **Step 1: Write the failing test**

Add to `scrapers/tests/test_export_document.py` (imports `AribaAttachment`, `BackgroundPdf`; the `seeded` fixture's solicitation is `document_number="5672751291"`):

```python
from toronto_bids.models import AribaAttachment, BackgroundPdf


def test_documents_nested_under_solicitation(seeded):
    db.upsert_row(seeded, AribaAttachment(
        document_number="5672751291", filename="site-plan.pdf",
        path="Appendix C2 - Planning Documents.zip/site-plan.pdf",
        file_size=12656277, crc32="deadbeef", zip_name="Doc5672751291.zip",
        zip_sha256="a" * 64), overwrite=True)
    db.upsert_row(seeded, BackgroundPdf(
        url="https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/binid",
        document_number="5672751291", kind="award_summary",
        local_path="/x/binid", sha256="b" * 64, text="..."), overwrite=True)
    seeded.commit()

    sol = next(s for s in build_export_document(seeded, generated_at="t")["solicitations"]
               if s["document_number"] == "5672751291")
    docs = {d["name"]: d for d in sol["documents"]}

    ariba = docs["site-plan.pdf"]
    assert ariba["source"] == "ariba_attachment"
    assert ariba["path"] == "Appendix C2 - Planning Documents.zip/site-plan.pdf"
    assert ariba["type"] == "pdf" and ariba["size_bytes"] == 12656277 and ariba["url"] is None
    assert "crc32" not in ariba and "sha256" not in ariba   # internal fields stay private

    form = docs["Award Summary Form.pdf"]
    assert form["source"] == "award_summary" and form["type"] == "pdf"
    assert form["size_bytes"] is None
    assert form["url"].startswith("https://secure.toronto.ca/")


def test_solicitation_without_documents_gets_empty_list(conn):
    db.upsert_row(conn, Solicitation(document_number="1", status="Open", source="odata"),
                  overwrite=True)
    conn.commit()
    sol = build_export_document(conn, generated_at="t")["solicitations"][0]
    assert sol["documents"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_document.py -k documents -v`
Expected: FAIL (`KeyError: 'documents'`).

- [ ] **Step 3: Implement the documents assembly**

In `scrapers/toronto_bids/export/document.py`, add a helper near the top (after the existing `_parse_json`):

```python
def _ext(name: str | None) -> str | None:
    if not name:
        return None
    dot = name.rfind(".")
    return name[dot + 1:].lower() if dot != -1 else None
```

Build `documents_by_doc` just before the `solicitations = []` loop (after `postings_by_doc` is built, ~line 58):

```python
    documents_by_doc: dict[str, list] = {}
    for att in _rows(conn, "SELECT document_number, filename, COALESCE(path, filename) AS path, "
                           "file_size FROM ariba_attachment ORDER BY document_number, path"):
        documents_by_doc.setdefault(att["document_number"], []).append({
            "source": "ariba_attachment",
            "name": att["filename"],
            "path": att["path"],
            "type": _ext(att["path"]),
            "size_bytes": att["file_size"],
            "url": None,
        })
    for form in _rows(conn, "SELECT document_number, url FROM background_pdf "
                            "WHERE kind='award_summary' ORDER BY document_number, url"):
        documents_by_doc.setdefault(form["document_number"], []).append({
            "source": "award_summary",
            "name": "Award Summary Form.pdf",
            "path": "Award Summary Form.pdf",
            "type": "pdf",
            "size_bytes": None,
            "url": form["url"],
        })
```

In the solicitation loop (lines 59-65), add the `documents` line:

```python
        sol["awards"] = awards_by_doc.get(doc, [])
        sol["ariba_postings"] = postings_by_doc.get(doc, [])
        sol["documents"] = documents_by_doc.get(doc, [])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_export_document.py -v` then the full suite `uv run pytest -q`.
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/document.py scrapers/tests/test_export_document.py
git commit -m "feat(export): per-solicitation documents index, incl. Award Summary Forms (#123)"
```

---

### Task 6: Run the real reindex + docs

Reindex the 33 captured bundles on disk and update CLAUDE.md.

**Files:**
- Modify: `CLAUDE.md` (the Ariba attachments section)

- [ ] **Step 1: Reindex the real store**

Run: `TB_DATA_DIR=/Users/alex/code/personal/toronto-bids/scrapers/files uv run tb enrich-ariba-attachments --reindex`
Expected: reports ~33 bundles reindexed; the file count rises above 520 as nested zips expand.

- [ ] **Step 2: Spot-check a nested expansion**

Run: `sqlite3 /Users/alex/code/personal/toronto-bids/scrapers/files/bids.sqlite "SELECT path FROM ariba_attachment WHERE path LIKE '%.zip/%' LIMIT 5;"`
Expected: paths showing files nested inside a `.zip` (e.g. `… Planning Documents.zip/…`).

- [ ] **Step 3: Update CLAUDE.md**

In the `### Ariba attachments` section of `CLAUDE.md`, add that the index is recursive (nested zips expanded to leaves, keyed on `path`), rebuilt from the on-disk bytes via `--reindex`, and that the per-solicitation `documents` array in the export unions Ariba files with the Award Summary Forms (which were previously orphaned). Note full-text/OCR remain deferred (see the deep-ingestion issue) and the surrogate-identity spine is #124.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: recursive Ariba attachment index + documents export (#123)"
```

## Notes for the implementer

- The `conn` fixture (`tests/conftest.py`) is an in-memory DB with `init_db` already run — new tables/columns are present.
- `_make_zip` writes a real zip on disk; `_zip_bytes` (added in Task 1) returns zip bytes for nesting one zip inside another.
- Do not add a dependency — everything here is stdlib (`zipfile`, `io`, `hashlib`).
- After Task 2 changes dependencies? No — no dependency change, so `uv.lock` is untouched. If you somehow change deps, re-lock and commit `uv.lock` (CI runs `--locked`).
