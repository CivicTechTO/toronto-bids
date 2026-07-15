# Toronto Bids Scraper — P3: Export / Publish Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the local SQLite store to a self-contained, solicitation-centric nested JSON document, behind a destination-agnostic `Exporter` interface, exposed as `tb export`.

**Architecture:** A pure builder (`build_export_document(conn) -> dict`) reads the store in one pass and assembles the nested shape — awards and Ariba postings nested under their solicitation by `document_number`, un-bridged postings and non-competitive contracts as their own top-level collections. An `Exporter` protocol is the publish seam; `JsonExporter` is its first implementation (serialize the document to a file). Later destinations (Parquet, a static site, an HTTP API) implement the same interface without touching the builder.

**Tech Stack:** Python 3.12+, `uv`, stdlib `json`/`sqlite3`, `pytest`. No new dependencies, no network — this reads the local store only.

## Global Constraints

- **Python 3.12+**, managed with **uv** only. All `uv`/`pytest` commands run from inside `scrapers/`. Package import name `toronto_bids`.
- **Builds on the merged P0/P1 + P2 package.** Reuse, do not duplicate: `store/db.py` (`connect`, `init_db`, `counts(conn) -> dict`), `config.py` (`DATA_DIR`, `DB_PATH`), the `cli.py` structure (`build_parser`, `_open_db`, `main` dispatch). The store's `conn` uses `row_factory = sqlite3.Row`, so `dict(row)` works.
- **Store tables** (columns are authoritative — read them from `store/schema.sql` if unsure): `solicitation` (PK `document_number`), `award` (`id`, `document_number`, `supplier_name_raw`, `supplier_id`, `award_amount`, `award_date`, `source`, `first_seen`, `last_seen`), `noncompetitive` (PK `workspace_number`), `ariba_posting` (PK `rfx_id`, has nullable `document_number`, and `raw_json`, `categories`), `sync_run`.
- **Export shape = solicitation-centric nested** (the approved decision):
  ```
  { "meta": { "generated_at", "counts", "sources" },
    "solicitations": [ { ...solicitation cols, "awards": [...], "ariba_postings": [...] } ],
    "noncompetitive": [ { ...noncompetitive cols } ],
    "unlinked_ariba_postings": [ { ...ariba_posting cols } ] }
  ```
- **Nest by `document_number`:** each award and each Ariba posting whose `document_number` matches a solicitation is nested under it. An Ariba posting with **NULL `document_number`** goes into top-level `unlinked_ariba_postings` — **nothing is dropped** (preserves the P2 archive-always guarantee through the export).
- **Non-competitive is a separate top-level array** (separate keyspace — never nested under a solicitation).
- **Column hygiene:** drop internal/surrogate columns from exported records — `award.id`, `*.supplier_id`, `*.odata_id`, and `ariba_posting.raw_json` (the bulky detail snapshot stays in the DB, not the published artifact). Drop the redundant `document_number` from a *nested* award/posting (it's implied by the parent). Parse `ariba_posting.categories` (a JSON-string column) back into a real array in the export.
- **Determinism:** `build_export_document(conn, generated_at=None)` accepts an optional `generated_at` so tests pin it; production defaults to `datetime.now(timezone.utc).isoformat()`. No other nondeterminism — order every query (`ORDER BY`).
- **The seam:** `Exporter` is a `runtime_checkable` `Protocol` with `name: str` and `export(self, conn, out_path, generated_at=None) -> Path`. `build_export_document` is the shared, format-independent source of truth; exporters differ by destination/format, not shape.
- **No new dependencies.**

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` (§5 linking model, §10 P3).

**Base branch:** `p2-ariba-discovery` (P3 stacks on P2).

---

## File Structure

New and modified files (all under `scrapers/`):

```
scrapers/
  toronto_bids/
    export/
      __init__.py          # CREATE: empty package marker
      document.py          # CREATE: build_export_document(conn, generated_at=None) -> dict (pure)
      base.py              # CREATE: Exporter protocol
      json_export.py       # CREATE: JsonExporter(Exporter)
    cli.py                 # MODIFY: add `export` subcommand
  tests/
    test_export_document.py  # CREATE: builder tests (seeded in-memory DB)
    test_json_export.py      # CREATE: JsonExporter file-write tests
    test_smoke.py            # MODIFY: add `tb export` CLI wiring test
```

---

### Task 1: `build_export_document` (pure nested builder)

The heart of P3: read the store in one pass and assemble the nested document. Pure and deterministic — no file I/O, no network.

**Files:**
- Create: `scrapers/toronto_bids/export/__init__.py` (empty)
- Create: `scrapers/toronto_bids/export/document.py`
- Create: `scrapers/tests/test_export_document.py`

**Interfaces:**
- Consumes: `db.counts(conn)`; a `conn` with `row_factory = sqlite3.Row`.
- Produces: `document.build_export_document(conn, generated_at: str | None = None) -> dict` with top-level keys `meta`, `solicitations`, `noncompetitive`, `unlinked_ariba_postings`. `meta` has `generated_at` (str), `counts` (dict), `sources` (list of `{source, status, finished_at, rows_fetched, rows_upserted}` ordered by `sync_run.id`). Each solicitation dict carries its own columns (minus `odata_id`) plus `awards` (list; each drops `id`/`supplier_id`/`document_number`) and `ariba_postings` (list; each drops `raw_json`/`document_number`, with `categories` parsed to a list). `noncompetitive` items drop `supplier_id`/`odata_id`. `unlinked_ariba_postings` items drop `raw_json` (but keep `document_number`, which is NULL), with `categories` parsed.

- [ ] **Step 1: Write the failing tests**

`scrapers/tests/test_export_document.py`:

```python
import json

import pytest

from toronto_bids.export.document import build_export_document
from toronto_bids.models import AribaPosting, Award, NonCompetitive, Solicitation
from toronto_bids.store import db


@pytest.fixture
def seeded(conn):
    # One solicitation with an award and a bridged Ariba posting.
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open",
                                     title="RFT Watermain", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Acme Co",
                              award_amount="1000", source="odata"), overwrite=True)
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number="5672751291",
                                     title="RFT Watermain", categories='["Sidewalk"]',
                                     raw_json='{"big":"blob"}', source="ariba_discovery"), overwrite=True)
    # An Ariba posting that never bridged (document_number is NULL).
    db.upsert_row(conn, AribaPosting(rfx_id="1110099999", document_number=None,
                                     title="Unbridged posting", categories='["Water"]',
                                     source="ariba_discovery"), overwrite=True)
    # A non-competitive contract (separate keyspace).
    db.upsert_row(conn, NonCompetitive(workspace_number="8614", supplier_name_raw="Sole Source Inc",
                                       reason="Emergency", source="odata"), overwrite=True)
    conn.commit()
    return conn


def test_meta_has_generated_at_counts_and_sources(seeded):
    db.finish_sync_run(seeded, db.start_sync_run(seeded, "odata_solicitations"),
                       status="ok", rows_fetched=5, rows_upserted=5)
    doc = build_export_document(seeded, generated_at="2026-07-15T00:00:00Z")
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["meta"]["counts"]["solicitation"] == 1
    assert doc["meta"]["sources"][-1]["source"] == "odata_solicitations"
    assert doc["meta"]["sources"][-1]["status"] == "ok"


def test_award_and_posting_nested_under_solicitation(seeded):
    doc = build_export_document(seeded, generated_at="t")
    sols = doc["solicitations"]
    assert len(sols) == 1
    sol = sols[0]
    assert sol["document_number"] == "5672751291"
    assert "odata_id" not in sol
    # award nested, internal cols dropped, redundant document_number dropped
    assert len(sol["awards"]) == 1
    assert sol["awards"][0]["supplier_name_raw"] == "Acme Co"
    assert "id" not in sol["awards"][0]
    assert "document_number" not in sol["awards"][0]
    # ariba posting nested, raw_json dropped, categories parsed to a list
    assert len(sol["ariba_postings"]) == 1
    assert sol["ariba_postings"][0]["rfx_id"] == "1110015885"
    assert "raw_json" not in sol["ariba_postings"][0]
    assert sol["ariba_postings"][0]["categories"] == ["Sidewalk"]
    assert "document_number" not in sol["ariba_postings"][0]


def test_unbridged_posting_goes_to_unlinked_not_dropped(seeded):
    doc = build_export_document(seeded, generated_at="t")
    unlinked = doc["unlinked_ariba_postings"]
    assert len(unlinked) == 1
    assert unlinked[0]["rfx_id"] == "1110099999"
    assert unlinked[0]["categories"] == ["Water"]
    assert "raw_json" not in unlinked[0]
    # It must NOT appear under any solicitation.
    assert all(p["rfx_id"] != "1110099999"
               for s in doc["solicitations"] for p in s["ariba_postings"])


def test_noncompetitive_is_separate_top_level(seeded):
    doc = build_export_document(seeded, generated_at="t")
    assert len(doc["noncompetitive"]) == 1
    assert doc["noncompetitive"][0]["workspace_number"] == "8614"
    assert "supplier_id" not in doc["noncompetitive"][0]


def test_document_is_json_serializable(seeded):
    doc = build_export_document(seeded, generated_at="t")
    json.dumps(doc)  # must not raise


def test_empty_store_produces_empty_collections(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["solicitations"] == []
    assert doc["noncompetitive"] == []
    assert doc["unlinked_ariba_postings"] == []
    assert doc["meta"]["counts"]["solicitation"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_export_document.py -v`
Expected: FAIL — `toronto_bids.export.document` not importable.

- [ ] **Step 3: Implement the builder**

`scrapers/toronto_bids/export/__init__.py`: empty file.

`scrapers/toronto_bids/export/document.py`:

```python
import json
from datetime import datetime, timezone

from toronto_bids.store import db


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _drop(record: dict, *keys) -> dict:
    return {k: v for k, v in record.items() if k not in keys}


def _parse_categories(posting: dict) -> dict:
    raw = posting.get("categories")
    if raw:
        try:
            posting["categories"] = json.loads(raw)
        except (TypeError, ValueError):
            pass  # leave the raw string if it isn't valid JSON
    return posting


def build_export_document(conn, generated_at: str | None = None) -> dict:
    """Assemble the solicitation-centric nested export document from the store.

    Pure and deterministic: no file I/O, every query ordered. Awards and Ariba
    postings are nested under their solicitation by document_number; postings
    with a NULL document_number go to unlinked_ariba_postings (nothing dropped).
    """
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()

    awards_by_doc: dict[str, list] = {}
    for award in _rows(conn, "SELECT * FROM award ORDER BY document_number, id"):
        awards_by_doc.setdefault(award["document_number"], []).append(
            _drop(award, "id", "supplier_id", "document_number")
        )

    postings_by_doc: dict[str, list] = {}
    unlinked: list = []
    for posting in _rows(conn, "SELECT * FROM ariba_posting ORDER BY rfx_id"):
        posting = _parse_categories(_drop(posting, "raw_json"))
        doc = posting.get("document_number")
        if doc:
            postings_by_doc.setdefault(doc, []).append(_drop(posting, "document_number"))
        else:
            unlinked.append(posting)

    solicitations = []
    for sol in _rows(conn, "SELECT * FROM solicitation ORDER BY document_number"):
        sol = _drop(sol, "odata_id")
        doc = sol["document_number"]
        sol["awards"] = awards_by_doc.get(doc, [])
        sol["ariba_postings"] = postings_by_doc.get(doc, [])
        solicitations.append(sol)

    noncompetitive = [
        _drop(nc, "supplier_id", "odata_id")
        for nc in _rows(conn, "SELECT * FROM noncompetitive ORDER BY workspace_number")
    ]

    sources = _rows(
        conn,
        "SELECT source, status, finished_at, rows_fetched, rows_upserted "
        "FROM sync_run ORDER BY id",
    )

    return {
        "meta": {
            "generated_at": generated_at,
            "counts": db.counts(conn),
            "sources": sources,
        },
        "solicitations": solicitations,
        "noncompetitive": noncompetitive,
        "unlinked_ariba_postings": unlinked,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_export_document.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/__init__.py scrapers/toronto_bids/export/document.py scrapers/tests/test_export_document.py
git commit -m "feat(scraper): add solicitation-centric export document builder"
```

---

### Task 2: `Exporter` protocol + `JsonExporter`

The publish seam and its first implementation.

**Files:**
- Create: `scrapers/toronto_bids/export/base.py`
- Create: `scrapers/toronto_bids/export/json_export.py`
- Create: `scrapers/tests/test_json_export.py`

**Interfaces:**
- Consumes: `build_export_document(conn, generated_at=None)`.
- Produces:
  - `base.Exporter` (`runtime_checkable` Protocol): `name: str`; `export(self, conn, out_path, generated_at=None) -> Path`.
  - `json_export.JsonExporter` with `name = "json"`; `export(conn, out_path, generated_at=None) -> Path` writes the document as indented UTF-8 JSON, creating parent dirs, and returns the resolved `Path`.

- [ ] **Step 1: Write the failing tests**

`scrapers/tests/test_json_export.py`:

```python
import json

from toronto_bids.export.base import Exporter
from toronto_bids.export.json_export import JsonExporter
from toronto_bids.models import Solicitation
from toronto_bids.store import db


def test_jsonexporter_satisfies_protocol():
    assert isinstance(JsonExporter(), Exporter)
    assert JsonExporter().name == "json"


def test_export_writes_valid_json_file(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="RFT", source="odata"),
                  overwrite=True)
    conn.commit()
    out = tmp_path / "nested" / "bids.json"
    result = JsonExporter().export(conn, out, generated_at="2026-07-15T00:00:00Z")
    assert result == out
    assert out.exists()  # parent dir created
    doc = json.loads(out.read_text())
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["solicitations"][0]["document_number"] == "5672751291"


def test_export_writes_utf8_content(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="Café Réno",
                                     source="odata"), overwrite=True)
    conn.commit()
    out = tmp_path / "bids.json"
    JsonExporter().export(conn, out, generated_at="t")
    # ensure_ascii=False keeps accented characters literal, not \u-escaped
    assert "Café Réno" in out.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_json_export.py -v`
Expected: FAIL — `toronto_bids.export.base` / `json_export` not importable.

- [ ] **Step 3: Implement the protocol and exporter**

`scrapers/toronto_bids/export/base.py`:

```python
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Exporter(Protocol):
    """The publish seam: turn the store into a published artifact at out_path.

    Implementations differ by destination/format, not by document shape — they
    all serialize build_export_document(conn). Future: Parquet, static site, API.
    """

    name: str

    def export(self, conn, out_path, generated_at: str | None = None) -> Path:
        ...
```

`scrapers/toronto_bids/export/json_export.py`:

```python
import json
from pathlib import Path

from toronto_bids.export.document import build_export_document


class JsonExporter:
    name = "json"

    def export(self, conn, out_path, generated_at: str | None = None) -> Path:
        document = build_export_document(conn, generated_at)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_json_export.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/export/base.py scrapers/toronto_bids/export/json_export.py scrapers/tests/test_json_export.py
git commit -m "feat(scraper): add Exporter seam and JsonExporter"
```

---

### Task 3: `tb export` CLI + live export + docs

Wire the exporter to the CLI, verify against the real store, and document it.

**Files:**
- Modify: `scrapers/toronto_bids/cli.py`
- Modify: `scrapers/tests/test_smoke.py`
- Modify: `scrapers/README.md`

**Interfaces:**
- Consumes: `JsonExporter`, `_open_db`, `config.DATA_DIR`.
- Produces: `tb export [--out PATH]`; default output `config.DATA_DIR / "export" / "bids.json"`. `cli.main` returns 0.

- [ ] **Step 1: Write the failing CLI test**

Add to `scrapers/tests/test_smoke.py`:

```python
def test_export_writes_default_path(tmp_path, monkeypatch, capsys):
    from toronto_bids.models import Solicitation
    monkeypatch.setattr("toronto_bids.config.DB_PATH", tmp_path / "bids.sqlite")
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    # Seed one row via a sync-less direct write path: open the db the CLI will use.
    from toronto_bids.store import db
    conn = db.connect(tmp_path / "bids.sqlite")
    db.init_db(conn)
    db.upsert_row(conn, Solicitation(document_number="5672751291", source="odata"), overwrite=True)
    conn.commit()
    conn.close()

    assert main(["export"]) == 0
    import json
    out = tmp_path / "export" / "bids.json"
    assert out.exists()
    doc = json.loads(out.read_text())
    assert doc["solicitations"][0]["document_number"] == "5672751291"
    assert "Exported" in capsys.readouterr().out
```

(`main` is already imported at the top of `test_smoke.py`.)

- [ ] **Step 2: Run the CLI test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_smoke.py -k export -v`
Expected: FAIL — argparse rejects the `export` subcommand / `main` falls through to help.

- [ ] **Step 3: Implement the `export` command**

In `scrapers/toronto_bids/cli.py`:

Add the import near the top:
```python
from toronto_bids.export.json_export import JsonExporter
```

Register the subcommand in `build_parser` (after the `status` parser):
```python
    p_export = sub.add_parser("export", help="Write the store to a nested JSON artifact")
    p_export.add_argument("--out", help="Output path (default: <DATA_DIR>/export/bids.json)")
```

Add the command handler (after `_cmd_status`):
```python
def _cmd_export(args) -> int:
    from pathlib import Path

    conn = _open_db()
    try:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "export" / "bids.json"
        written = JsonExporter().export(conn, out_path)
        counts = db.counts(conn)
        print(f"Exported {counts['solicitation']} solicitations to {written}")
    finally:
        conn.close()
    return 0
```

Add the dispatch in `main` (before the `parser.print_help()` fallthrough):
```python
    if args.command == "export":
        return _cmd_export(args)
```

- [ ] **Step 4: Run the full suite**

Run: `cd scrapers && uv run pytest -v`
Expected: every test PASSES, output pristine.

- [ ] **Step 5: Live export smoke check (reads a real synced DB — manual, not a test)**

Run (populates a real DB, then exports it):
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-p3 uv run tb sync && TB_DATA_DIR=/tmp/tb-p3 uv run tb export
```
Expected: `tb sync` completes (all sources); `tb export` prints `Exported <N> solicitations to /tmp/tb-p3/export/bids.json`. Then sanity-check the artifact:
```bash
python3 - <<'PY'
import json
d = json.load(open("/tmp/tb-p3/export/bids.json"))
print("solicitations:", len(d["solicitations"]))
print("noncompetitive:", len(d["noncompetitive"]))
print("unlinked_ariba_postings:", len(d["unlinked_ariba_postings"]))
s = next(x for x in d["solicitations"] if x["ariba_postings"])
print("example nested posting rfx_id:", s["ariba_postings"][0]["rfx_id"],
      "| categories is list:", isinstance(s["ariba_postings"][0]["categories"], list))
PY
```
(If `python3` is unavailable, use `uv run python - <<'PY' ... PY` instead.) Expected: solicitations in the thousands; some solicitations carry nested `ariba_postings`; `unlinked_ariba_postings` holds the un-bridged ones; `categories` is a list. Record the numbers in the report. Do not block the commit on exact counts.

- [ ] **Step 6: Update the README**

In `scrapers/README.md`, under the Usage section (near `tb sync` / `tb status`), add:

```markdown
- `uv run tb export [--out PATH]` — write the whole store to a single
  solicitation-centric nested JSON artifact (default `<DATA_DIR>/export/bids.json`):
  each solicitation with its `awards` and `ariba_postings` nested by `document_number`,
  plus top-level `noncompetitive` and `unlinked_ariba_postings` (Ariba postings that
  never bridged to a document number). This is the publish seam — the `Exporter`
  interface lets other destinations/formats be added without changing the document shape.
```

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_smoke.py scrapers/README.md
git commit -m "feat(scraper): add tb export command and document the publish seam"
```

---

## Self-Review

**1. Spec coverage (design §5, §10 P3):**
- Solicitation-centric nested shape with awards + ariba_postings nested by `document_number` → Task 1. ✓
- Un-bridged postings preserved in `unlinked_ariba_postings` (archive-always through export) → Task 1 + `test_unbridged_posting_goes_to_unlinked_not_dropped`. ✓
- Non-competitive as a separate top-level array (separate keyspace) → Task 1 + `test_noncompetitive_is_separate_top_level`. ✓
- Column hygiene (drop `award.id`/`supplier_id`/`odata_id`/`raw_json`, redundant nested `document_number`; parse `categories`) → Task 1. ✓
- `meta` with `generated_at`/`counts`/`sources` → Task 1. ✓
- Destination-agnostic `Exporter` seam + JSON first implementation → Task 2. ✓
- `tb export` CLI → Task 3. ✓
- Deterministic tests via `generated_at` → Tasks 1–3. ✓
- Out of scope by design (P4 attachments, P5 TMMIS/PDF, and non-JSON exporters like Parquet/Azure/static-site — the seam exists for them but they are not built here) → not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; every command shows expected output. No `NotImplementedError` stubs in this plan. ✓

**3. Type consistency:** `build_export_document(conn, generated_at=None) -> dict` signature identical across Tasks 1, 2, 3. `JsonExporter.export(conn, out_path, generated_at=None) -> Path` matches the `Exporter` protocol (Task 2) and the CLI caller (Task 3). Top-level document keys (`meta`, `solicitations`, `noncompetitive`, `unlinked_ariba_postings`) and nested keys (`awards`, `ariba_postings`) are used identically in the builder (Task 1), the exporter tests (Task 2), and the CLI/live checks (Task 3). `db.counts` returns a dict keyed by table name (`counts["solicitation"]`), consistent across Tasks 1 and 3. ✓
