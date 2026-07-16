# Toronto Bids Scraper — P5a: Canonical Supplier Dimension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the free-text supplier names scattered across awards, non-competitive contracts, and suspended firms into one canonical `supplier` dimension, and back-link every row to it — so a consumer can answer *"which City contracts / suspensions belong to this supplier?"*.

**Architecture:** A pure canonicalization function (`supplier_key`) turns any raw name into a deterministic grouping key. A `build_supplier_dimension(conn)` linking pass — run after all sources sync — collects every `supplier_name_raw`, groups by key, upserts one `supplier` row per group, and backfills the `supplier_id` foreign key on the source rows. Idempotent and re-runnable. The export gains a top-level `suppliers` array and keeps the `supplier_id` join keys it previously dropped.

**Tech Stack:** Python 3.12+, `uv`, stdlib `json`/`sqlite3`/`re`, `pytest`. No network, no new dependencies — this is a local linking pass.

**Scope note:** This is **P5a**. P5b (TMMIS council agenda-items + background-file PDFs) is a separate plan. This plan does not touch the network, Playwright, or PDFs.

## Global Constraints

- **Python 3.12+**, managed with **uv** only. All `uv`/`pytest` commands run from inside `scrapers/`. Package import name `toronto_bids`. No new dependencies.
- **Builds on the merged P0–P4a package.** Reuse, do not duplicate: `store/db.py` (`connect`, `init_db`, `counts`, `upsert_row(conn, row, *, overwrite)`, `_upsert_keyed`), `store/schema.sql`, `models.py`, `pipeline.py` (`sync`, `run_source`, `default_sources`, `db.start_sync_run`/`finish_sync_run`), `export/document.py` (`build_export_document`, `_rows`, `_drop`), `linking/document_number.py` (sibling module home), `cli.py`.
- **Canonicalization is deterministic and conservative** (`linking/supplier.py::supplier_key`): strip a trailing `(Submitted by: …)` parenthetical, lowercase, remove every character that is not `[a-z0-9 ]`, collapse runs of whitespace to a single space, strip ends. **Do NOT strip legal suffixes** (`Inc`, `Ltd`, …) — that would over-merge distinct entities. This merges real spelling variants (verified live 2026-07-15: `Compugen Inc.`≡`Compugen Inc`; `Direct Construction Company limited`≡`…Limited`; `QRX TECHNOLOGY GROUP INC`→`qrx technology group inc`) without collapsing genuinely different firms.
- **`supplier` table:** `supplier_id INTEGER PRIMARY KEY AUTOINCREMENT`, `supplier_key TEXT UNIQUE NOT NULL`, `display_name TEXT`, `variants TEXT` (JSON array of the distinct raw names), `first_seen`/`last_seen` (TEXT `datetime('now')`).
- **Sources of names:** `award.supplier_name_raw`, `noncompetitive.supplier_name_raw`, `suspended_firm.supplier_name_raw`. `award` and `noncompetitive` already carry a nullable `supplier_id` column; **`suspended_firm` does not — add it.**
- **The linking pass is idempotent** — re-running `build_supplier_dimension` on the same data yields the same suppliers and FKs (upsert on `supplier_key`; recompute FKs each run). A name whose `supplier_key` is empty (blank/garbage name) is skipped (no supplier row, `supplier_id` left NULL).
- **Never delete; `first_seen`/`last_seen`** on `supplier` via the existing `_upsert_keyed` (which touches `last_seen`).
- **Pipeline:** the linking pass runs **after** all sources in `pipeline.sync`, recorded as a `sync_run` named `supplier_dimension`, wrapped so its failure is isolated (does not raise out of `sync`).
- **Export:** stop dropping `supplier_id` from `award` / `noncompetitive` / `suspended_firm`, add a top-level `suppliers` array. Keep the surrogate-`id` drops (`award.id`, `suspended_firm.id`) and `odata_id` drops as-is.
- **No network in unit tests** — seed the store directly; the only network is the Task 4 live smoke.

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` (§3.2 supplier identity, §5 `dim_supplier`, §10 P5).

**Base branch:** `p5-enrichment` (off `p4-tier2`).

---

## File Structure

New and modified files (all under `scrapers/`):

```
scrapers/
  toronto_bids/
    models.py                  # MODIFY: add Supplier dataclass
    store/
      schema.sql               # MODIFY: add supplier table; add supplier_id to suspended_firm
      db.py                    # MODIFY: _SUPPLIER_COLS, upsert_row branch, counts()
    linking/
      supplier.py              # CREATE: supplier_key + build_supplier_dimension
    pipeline.py                # MODIFY: run build_supplier_dimension after sources
    export/
      document.py              # MODIFY: keep supplier_id; add top-level suppliers array
  tests/
    test_supplier_key.py       # CREATE: canonicalization tests (real variants)
    test_supplier_dimension.py # CREATE: build_supplier_dimension linking tests
    test_db.py                 # MODIFY: supplier upsert/counts tests
    test_pipeline.py           # MODIFY: linking pass runs after sources, isolated
    test_export_document.py    # MODIFY: suppliers array + supplier_id retained
```

---

### Task 1: `Supplier` model + `supplier` table + `suspended_firm.supplier_id` + store wiring

**Files:**
- Modify: `scrapers/toronto_bids/models.py`
- Modify: `scrapers/toronto_bids/store/schema.sql`
- Modify: `scrapers/toronto_bids/store/db.py`
- Modify: `scrapers/tests/test_db.py`

**Interfaces:**
- Produces: `models.Supplier` (frozen dataclass: `supplier_key: str`, `display_name: str | None = None`, `variants: str | None = None`); `supplier` table keyed `UNIQUE(supplier_key)`; `db.upsert_row` routes `Supplier` (conflict key `supplier_key`); `db.counts` includes `"supplier"`; `suspended_firm` gains a nullable `supplier_id` column.

- [ ] **Step 1: Add the `Supplier` dataclass**

Append to `scrapers/toronto_bids/models.py`:

```python
@dataclass(frozen=True)
class Supplier:
    supplier_key: str
    display_name: str | None = None
    variants: str | None = None
```

- [ ] **Step 2: Add the `supplier` table and `suspended_firm.supplier_id`**

In `scrapers/toronto_bids/store/schema.sql`, append the `supplier` table:

```sql

-- supplier is the canonical supplier dimension: one row per normalized supplier_key,
-- with the raw name variants that mapped to it. award/noncompetitive/suspended_firm carry a
-- nullable supplier_id FK, backfilled by the build_supplier_dimension linking pass.
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key  TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    variants      TEXT,
    first_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen     TEXT NOT NULL DEFAULT (datetime('now'))
);
```

And add a `supplier_id` column to the existing `suspended_firm` table definition (insert the column line after `council_authority TEXT,`):

```sql
    council_authority  TEXT,
    supplier_id        INTEGER,
```

(`init_db` runs `CREATE TABLE IF NOT EXISTS`, so a fresh DB gets the new column; there is no persistent production DB to migrate.)

- [ ] **Step 3: Write the failing store tests**

Add to `scrapers/tests/test_db.py` (extend the `from toronto_bids.models import …` line to include `Supplier`):

```python
def test_upsert_supplier_is_idempotent(conn):
    from toronto_bids.models import Supplier
    s = Supplier(supplier_key="compugen inc", display_name="Compugen Inc.", variants='["Compugen Inc."]')
    db.upsert_row(conn, s, overwrite=True)
    db.upsert_row(conn, s, overwrite=True)
    assert db.counts(conn)["supplier"] == 1


def test_upsert_supplier_updates_variants(conn):
    from toronto_bids.models import Supplier
    db.upsert_row(conn, Supplier(supplier_key="compugen inc", display_name="Compugen Inc",
                                 variants='["Compugen Inc"]'), overwrite=True)
    db.upsert_row(conn, Supplier(supplier_key="compugen inc", display_name="Compugen Inc.",
                                 variants='["Compugen Inc", "Compugen Inc."]'), overwrite=True)
    row = conn.execute("SELECT variants FROM supplier WHERE supplier_key='compugen inc'").fetchone()
    assert row["variants"] == '["Compugen Inc", "Compugen Inc."]'


def test_counts_includes_supplier(conn):
    assert "supplier" in db.counts(conn)


def test_suspended_firm_has_supplier_id_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suspended_firm)")}
    assert "supplier_id" in cols
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_db.py -k "supplier or supplier_id" -v`
Expected: FAIL — `Supplier` not importable / `counts` has no `supplier` key / no `supplier_id` column.

- [ ] **Step 5: Wire the store**

In `scrapers/toronto_bids/store/db.py`:

Extend the models import:
```python
from toronto_bids.models import Award, NonCompetitive, Solicitation, AribaPosting, SuspendedFirm, Supplier
```

Add the column list after `_SUSPENDED_COLS`:
```python
_SUPPLIER_COLS = ["supplier_key", "display_name", "variants"]
```

Add a branch in `upsert_row` (before the final `else`):
```python
    elif isinstance(row, Supplier):
        values = [getattr(row, c) for c in _SUPPLIER_COLS]
        _upsert_keyed(conn, "supplier", _SUPPLIER_COLS, values, ["supplier_key"], overwrite)
```

Add `"supplier"` to the `counts` table list (before `"sync_run"`):
```python
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting",
              "suspended_firm", "supplier", "sync_run"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/models.py scrapers/toronto_bids/store scrapers/tests/test_db.py
git commit -m "feat(scraper): add supplier dimension table, model, and suspended_firm.supplier_id"
```

---

### Task 2: `supplier_key` canonicalization

**Files:**
- Create: `scrapers/toronto_bids/linking/supplier.py`
- Create: `scrapers/tests/test_supplier_key.py`

**Interfaces:**
- Produces: `supplier.supplier_key(raw: str | None) -> str` — the deterministic grouping key (may be `""` for blank/garbage input).

- [ ] **Step 1: Write the failing tests (real variants)**

`scrapers/tests/test_supplier_key.py`:

```python
import pytest

from toronto_bids.linking.supplier import supplier_key


@pytest.mark.parametrize(
    "a,b",
    [
        ("Compugen Inc.", "Compugen Inc"),                                  # trailing period
        ("Direct Construction Company limited", "Direct Construction Company Limited"),  # case
        ("QRX TECHNOLOGY GROUP INC", "Qrx Technology Group Inc"),           # all-caps vs title
        ("Joe Pace & Sons Contracting Inc.", "JOE PACE & SONS CONTRACTING INC"),  # case + punct
        ("Acme  Co", "Acme Co"),                                            # collapsed whitespace
    ],
)
def test_variants_share_a_key(a, b):
    assert supplier_key(a) == supplier_key(b)
    assert supplier_key(a) != ""


def test_key_is_lowercase_alnum_spaces_only():
    assert supplier_key("QRX TECHNOLOGY GROUP INC") == "qrx technology group inc"
    assert supplier_key("Joe Pace & Sons Contracting Inc.") == "joe pace sons contracting inc"


def test_strips_submitted_by_suffix():
    assert supplier_key("SCA Office Solutions (Submitted by: Acme Reseller)") == \
        supplier_key("SCA Office Solutions")


def test_distinct_entities_stay_distinct():
    # Legal suffixes are NOT stripped, so Inc vs Ltd remain different keys.
    assert supplier_key("Capital Sewer Services Inc.") != supplier_key("Capital Sewer Services Ltd.")


@pytest.mark.parametrize("raw", [None, "", "   ", "()", "!!!"])
def test_blank_or_garbage_yields_empty_key(raw):
    assert supplier_key(raw) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_supplier_key.py -v`
Expected: FAIL — `toronto_bids.linking.supplier` not importable.

- [ ] **Step 3: Implement `supplier_key`**

`scrapers/toronto_bids/linking/supplier.py`:

```python
import re

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def supplier_key(raw: str | None) -> str:
    """Deterministic grouping key for a raw supplier name.

    Drops a trailing "(Submitted by: …)" note, lowercases, removes every character
    that is not [a-z0-9 ], and collapses whitespace. Legal suffixes (Inc, Ltd, …) are
    intentionally kept so genuinely different entities are not merged. Returns "" for
    blank/garbage input (caller skips those).
    """
    if raw is None:
        return ""
    text = _SUBMITTED_BY.sub(" ", str(raw))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_supplier_key.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/linking/supplier.py scrapers/tests/test_supplier_key.py
git commit -m "feat(scraper): add supplier_key canonicalization"
```

---

### Task 3: `build_supplier_dimension` linking pass

**Files:**
- Modify: `scrapers/toronto_bids/linking/supplier.py`
- Create: `scrapers/tests/test_supplier_dimension.py`

**Interfaces:**
- Consumes: `supplier_key`, `db.upsert_row`, `models.Supplier`, a `conn` with `row_factory = sqlite3.Row`.
- Produces: `supplier.build_supplier_dimension(conn) -> int` — builds/refreshes the `supplier` table and backfills `supplier_id` on `award`, `noncompetitive`, `suspended_firm`. Returns the number of distinct suppliers. Idempotent.

- [ ] **Step 1: Write the failing tests**

`scrapers/tests/test_supplier_dimension.py`:

```python
import json

from toronto_bids.linking.supplier import build_supplier_dimension, supplier_key
from toronto_bids.models import Award, NonCompetitive, SuspendedFirm
from toronto_bids.store import db


def _seed(conn):
    # Two spellings of the same supplier across award + noncompetitive.
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5749398870", supplier_name_raw="Compugen Inc",
                              source="ckan_awarded"), overwrite=True)
    db.upsert_row(conn, NonCompetitive(workspace_number="8614", supplier_name_raw="Accuworx Inc",
                                       source="odata"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    conn.commit()


def test_merges_spelling_variants_into_one_supplier(conn):
    _seed(conn)
    n = build_supplier_dimension(conn)
    # Compugen (2 spellings) + Accuworx + Duron = 3 distinct suppliers.
    assert n == 3
    assert db.counts(conn)["supplier"] == 3
    compugen = conn.execute(
        "SELECT variants FROM supplier WHERE supplier_key=?", (supplier_key("Compugen Inc."),)
    ).fetchone()
    assert set(json.loads(compugen["variants"])) == {"Compugen Inc.", "Compugen Inc"}


def test_backfills_supplier_id_on_all_source_tables(conn):
    _seed(conn)
    build_supplier_dimension(conn)
    key = supplier_key("Compugen Inc.")
    sid = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key=?", (key,)).fetchone()[0]
    # both award rows (different spellings) point at the same supplier_id
    award_ids = {r[0] for r in conn.execute("SELECT supplier_id FROM award")}
    assert sid in award_ids
    assert conn.execute(
        "SELECT COUNT(*) FROM award WHERE supplier_id=?", (sid,)
    ).fetchone()[0] == 2
    # noncompetitive + suspended_firm are also linked
    assert conn.execute("SELECT supplier_id FROM noncompetitive WHERE workspace_number='8614'").fetchone()[0] is not None
    assert conn.execute("SELECT supplier_id FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'").fetchone()[0] is not None


def test_is_idempotent(conn):
    _seed(conn)
    build_supplier_dimension(conn)
    build_supplier_dimension(conn)
    assert db.counts(conn)["supplier"] == 3  # no duplicate suppliers on re-run


def test_blank_supplier_name_is_skipped(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="", source="odata"),
                  overwrite=True)
    conn.commit()
    assert build_supplier_dimension(conn) == 0
    assert db.counts(conn)["supplier"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_supplier_dimension.py -v`
Expected: FAIL — `build_supplier_dimension` not defined.

- [ ] **Step 3: Implement the linking pass**

Append to `scrapers/toronto_bids/linking/supplier.py` (add the imports at the top of the file):

```python
import json

from toronto_bids.models import Supplier
from toronto_bids.store import db

# (source table, its primary-key column) for the tables carrying supplier_name_raw + supplier_id.
_SUPPLIER_TABLES = [
    ("award", "id"),
    ("noncompetitive", "workspace_number"),
    ("suspended_firm", "id"),
]


def build_supplier_dimension(conn) -> int:
    """Build/refresh the supplier dimension and backfill supplier_id FKs. Idempotent.

    Returns the number of distinct suppliers.
    """
    # 1. Collect every (row pk, raw name, key) and group raw names by key.
    variants_by_key: dict[str, set] = {}
    row_keys: list[tuple[str, object, str]] = []  # (table, pk, key)
    for table, pk in _SUPPLIER_TABLES:
        for row in conn.execute(f"SELECT {pk} AS pk, supplier_name_raw FROM {table}"):
            raw = row["supplier_name_raw"]
            key = supplier_key(raw)
            if not key:
                continue
            variants_by_key.setdefault(key, set()).add(raw)
            row_keys.append((table, row["pk"], key))

    # 2. Upsert one supplier per key (deterministic display_name + variants).
    for key, variants in variants_by_key.items():
        ordered = sorted(variants)
        db.upsert_row(
            conn,
            Supplier(supplier_key=key, display_name=ordered[0], variants=json.dumps(ordered)),
            overwrite=True,
        )

    # 3. Map key -> supplier_id, then backfill the FK on each source row.
    id_by_key = {r["supplier_key"]: r["supplier_id"]
                 for r in conn.execute("SELECT supplier_key, supplier_id FROM supplier")}
    for table, pk in _SUPPLIER_TABLES:
        for row_table, row_pk, key in row_keys:
            if row_table != table:
                continue
            conn.execute(
                f"UPDATE {table} SET supplier_id = ? WHERE {pk} = ?",
                (id_by_key[key], row_pk),
            )
    conn.commit()
    return len(variants_by_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_supplier_dimension.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/linking/supplier.py scrapers/tests/test_supplier_dimension.py
git commit -m "feat(scraper): add build_supplier_dimension linking pass"
```

---

### Task 4: Wire into pipeline + export + live smoke + docs

**Files:**
- Modify: `scrapers/toronto_bids/pipeline.py`
- Modify: `scrapers/toronto_bids/export/document.py`
- Modify: `scrapers/tests/test_pipeline.py`
- Modify: `scrapers/tests/test_export_document.py`
- Modify: `scrapers/README.md`

**Interfaces:**
- Consumes: `build_supplier_dimension`, `db.start_sync_run`/`finish_sync_run`, `build_export_document`.
- Produces: `pipeline.sync` runs the linking pass after sources (recorded as `sync_run` `supplier_dimension`); the export gains a top-level `suppliers` array and retains `supplier_id` on nested award/noncompetitive/suspended_firm.

- [ ] **Step 1: Write the failing pipeline test**

Add to `scrapers/tests/test_pipeline.py`:

```python
def test_sync_runs_supplier_dimension_after_sources(conn):
    from toronto_bids import pipeline
    from toronto_bids.models import Award
    good = FakeSource("odata_solicitations", [
        Award("3303123110", supplier_name_raw="Compugen Inc.", source="odata"),
    ])
    pipeline.sync(conn, http=None, sources=[good])
    # the linking pass ran: a supplier exists and a sync_run named supplier_dimension is recorded
    assert db.counts(conn)["supplier"] == 1
    row = conn.execute("SELECT status FROM sync_run WHERE source='supplier_dimension'").fetchone()
    assert row is not None and row["status"] == "ok"


def test_sync_supplier_dimension_failure_is_isolated(conn, monkeypatch):
    from toronto_bids import pipeline
    def boom(_conn):
        raise RuntimeError("link exploded")
    monkeypatch.setattr(pipeline, "build_supplier_dimension", boom)
    pipeline.sync(conn, http=None, sources=[])  # no sources; linking still runs and fails safely
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='supplier_dimension'").fetchone()
    assert row["status"] == "failed"
    assert "link exploded" in row["error"]
```

(The `FakeSource` helper and `conn` fixture already exist in `test_pipeline.py` / conftest.)

- [ ] **Step 2: Run the pipeline test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_pipeline.py -k supplier_dimension -v`
Expected: FAIL — no `supplier_dimension` sync_run / `pipeline.build_supplier_dimension` not present.

- [ ] **Step 3: Wire the linking pass into `sync`**

In `scrapers/toronto_bids/pipeline.py`, add the import:
```python
from toronto_bids.linking.supplier import build_supplier_dimension
```

Replace the `sync` function with one that runs the linking pass after sources, isolated and recorded:
```python
def sync(conn, http, sources=None, only=None) -> None:
    sources = sources if sources is not None else default_sources()
    if only is not None:
        wanted = set(only)
        sources = [s for s in sources if s.name in wanted]
    for source in sources:
        run_source(conn, http, source)
    _run_supplier_dimension(conn)


def _run_supplier_dimension(conn) -> None:
    """Rebuild the supplier dimension after sources. Isolated: never raises out of sync."""
    run_id = db.start_sync_run(conn, "supplier_dimension")
    try:
        n = build_supplier_dimension(conn)
        db.finish_sync_run(conn, run_id, status="ok", rows_fetched=n, rows_upserted=n)
    except Exception as exc:
        conn.commit()
        db.finish_sync_run(conn, run_id, status="failed", error=str(exc))
```

(Referencing `build_supplier_dimension` as a module-level name — imported at top — lets `test_sync_supplier_dimension_failure_is_isolated` monkeypatch `pipeline.build_supplier_dimension`.)

- [ ] **Step 4: Write the failing export test**

Add to `scrapers/tests/test_export_document.py`:

```python
def test_export_has_suppliers_array_and_retains_supplier_id(conn):
    from toronto_bids.models import Award, Solicitation, Supplier
    from toronto_bids.store import db as _db
    _db.upsert_row(conn, Supplier(supplier_key="compugen inc", display_name="Compugen Inc.",
                                  variants='["Compugen Inc."]'), overwrite=True)
    sid = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key='compugen inc'").fetchone()[0]
    _db.upsert_row(conn, Solicitation(document_number="3303123110", source="odata"), overwrite=True)
    _db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                               source="odata"), overwrite=True)
    conn.execute("UPDATE award SET supplier_id=? WHERE document_number='3303123110'", (sid,))
    conn.commit()

    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suppliers"]) == 1
    assert doc["suppliers"][0]["display_name"] == "Compugen Inc."
    # supplier_id is retained on nested awards so consumers can join to suppliers[]
    award = doc["solicitations"][0]["awards"][0]
    assert award["supplier_id"] == sid


def test_export_suppliers_empty_when_none(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["suppliers"] == []
```

- [ ] **Step 5: Run the export test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_export_document.py -k suppliers -v`
Expected: FAIL — `doc["suppliers"]` KeyError / `supplier_id` was dropped from the nested award.

- [ ] **Step 6: Update the export document**

In `scrapers/toronto_bids/export/document.py`:

Stop dropping `supplier_id` from awards — change the award loop's `_drop`:
```python
        cleaned = _drop(award, "id")
```
(was `_drop(award, "id", "supplier_id")`)

Stop dropping `supplier_id` from noncompetitive — change:
```python
    noncompetitive = [
        _drop(nc, "odata_id")
        for nc in _rows(conn, "SELECT * FROM noncompetitive ORDER BY workspace_number")
    ]
```
(was `_drop(nc, "supplier_id", "odata_id")`)

Add a `suppliers` block after the `suspended_firms` block:
```python
    suppliers = [
        _drop(s, "supplier_key")
        for s in _rows(conn, "SELECT * FROM supplier ORDER BY display_name")
    ]
```

Add `"suppliers": suppliers,` to the returned dict (alongside `suspended_firms`).

(`suspended_firm` already exports via `_drop(firm, "id")`, so its `supplier_id` is retained automatically.)

- [ ] **Step 7: Run the full suite**

Run: `cd scrapers && uv run pytest -v`
Expected: every test PASSES, output pristine.

- [ ] **Step 8: Live smoke check (full sync — network — manual, not a test)**

Run:
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-p5a uv run tb sync && uv run tb status
```
Expected: `tb status` shows a `supplier` count in the thousands. Then verify the dimension actually merges variants and backfills FKs:
```bash
sqlite3 /tmp/tb-p5a/bids.sqlite "SELECT COUNT(*) suppliers, (SELECT COUNT(*) FROM supplier WHERE json_array_length(variants) > 1) merged_multi FROM supplier;"
sqlite3 /tmp/tb-p5a/bids.sqlite "SELECT display_name, variants FROM supplier WHERE json_array_length(variants) > 1 LIMIT 5;"
sqlite3 /tmp/tb-p5a/bids.sqlite "SELECT (SELECT COUNT(*) FROM award WHERE supplier_id IS NOT NULL) awards_linked, (SELECT COUNT(*) FROM award) awards_total;"
sqlite3 /tmp/tb-p5a/bids.sqlite "SELECT status, rows_upserted FROM sync_run WHERE source='supplier_dimension' ORDER BY id DESC LIMIT 1;"
```
Expected: `merged_multi` > 0 (real spelling variants merged); most awards have a non-NULL `supplier_id`; the `supplier_dimension` sync_run is `ok`. Record the actual numbers in the report. Do not block the commit on exact counts.

- [ ] **Step 9: Update the README**

In `scrapers/README.md`, add to the sources/pipeline description:
```markdown
- **Supplier dimension** (`supplier` table) — after every sync, a linking pass canonicalizes
  the free-text supplier names across awards, non-competitive contracts, and suspended firms
  into one `supplier` row per firm (merging spelling/case/punctuation variants; legal suffixes
  kept so distinct entities stay distinct) and backfills a `supplier_id` FK on those rows. The
  export includes a top-level `suppliers` array; each award/non-competitive/suspended-firm
  record keeps its `supplier_id` so you can answer "which contracts belong to this supplier?".
```

- [ ] **Step 10: Commit**

```bash
git add scrapers/toronto_bids/pipeline.py scrapers/toronto_bids/export/document.py scrapers/tests/test_pipeline.py scrapers/tests/test_export_document.py scrapers/README.md
git commit -m "feat(scraper): run supplier-dimension linking pass in sync; export suppliers"
```

---

## Self-Review

**1. Spec coverage (design §3.2, §5, §10 P5a):**
- Canonical supplier dimension from free-text names (§5 `dim_supplier`) → Tasks 1–3. ✓
- Conservative normalization (lowercase + strip punctuation, no legal-suffix stripping; drop `(Submitted by:…)`) matching the recon method (§3.2) → Task 2. ✓
- Backfill `supplier_id` across award / noncompetitive / suspended_firm; suspended_firm gains the column → Tasks 1, 3. ✓
- Idempotent, re-runnable linking pass → Task 3 (`test_is_idempotent`). ✓
- Run after sources in the pipeline, isolated + recorded → Task 4. ✓
- Export the dimension + retain join keys → Task 4. ✓
- Out of scope by design (TMMIS council + PDFs = P5b; true edit-distance fuzzy matching = future refinement) → not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; every command shows expected output. No `NotImplementedError` stubs. ✓

**3. Type consistency:** `Supplier(supplier_key, display_name, variants)` fields match the dataclass (Task 1), `_SUPPLIER_COLS` (Task 1), and the linking-pass constructor (Task 3). `supplier_key(raw) -> str` signature identical across Tasks 2, 3. `build_supplier_dimension(conn) -> int` matches its caller in `_run_supplier_dimension` (Task 4) and is referenced as `pipeline.build_supplier_dimension` (module-level import) so the monkeypatch test works. `db.counts` gains `"supplier"` (Task 1), rendered by `tb status` and asserted in Task 1. The export adds `suppliers` used identically in the builder and tests (Task 4), and the `_drop` changes retain `supplier_id` on the exact tables the join needs. `_SUPPLIER_TABLES` pk columns (`award.id`, `noncompetitive.workspace_number`, `suspended_firm.id`) match the real schema. ✓
