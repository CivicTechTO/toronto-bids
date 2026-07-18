# bids&tenders Portal Listing Capture (#135) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Arm a verified plain-HTTP capture of TRCA/Zoo bids&tenders listing metadata into `agency_solicitation`, running nightly and safely no-opping while the portals are empty; parser is provisional until a real record can validate it.

**Architecture:** `sources/bids_tenders.py` replaces its gate-only stub with a real source: `fetch_listings` (own `httpx.Client` doing the antiforgery session dance, paged, rate-limited), `parse_listing` (pure, PROVISIONAL — mapped to the JS-documented schema), `store_listings` (COALESCE upsert), `record_listings` (dump raw JSON for future fixtures). Wired into `tb enrich-agencies --portal [--record]` and an isolated `tb nightly` step.

**Tech Stack:** Python 3.12, `uv`, httpx (already a dep), sqlite3, pytest (offline).

## Global Constraints

- **Never send the `sort` query param** to `/Tender/Search` — its space/comma triggers a server error (verified live 2026-07-18). Query params are `status,limit,start,dir,from,to` only.
- Use the **first** `__RequestVerificationToken` on the landing page (the `#bidDetailAntiForgery`-scoped one 302s).
- The session needs one `httpx.Client` across the landing GET and the search POSTs so the antiforgery cookie persists; extract `NodeId` + token from the landing HTML.
- **Rate-limit**: a deliberate `time.sleep` (default 1.5 s) before every search POST — the explicit "low-impact / off-peak" condition in both permission files.
- **No bid documents, ever** — listings only (Vendor clickwrap). No login, no writes, read-only.
- `parse_listing` is **PROVISIONAL** (module docstring says so): validated only against a synthetic fixture until a real record is captured. The `agency_award`-from-awarded path is **not built** in this plan.
- Portals are empty today (`total=0`); every path must no-op cleanly on empty. Per-body isolation: TRCA failing never stops Zoo.
- Tests offline, no network: `cd scrapers && uv run pytest`. Commit messages end with the project's Co-Authored-By + Claude-Session trailer. Branch: `feat-135-portal-listings` (already checked out; the spec is committed there).

---

### Task 1: `fetch_listings` — the verified session + paging (impure) and its gate

**Files:**
- Modify: `scrapers/toronto_bids/sources/bids_tenders.py` (replace the stub)
- Modify: `scrapers/tests/test_agencies.py` (update the two gate tests, which reference the old stub behaviour)

**Interfaces:**
- Consumes: `config.BIDS_TENDERS_PORTALS`, `config.USER_AGENT`, `config.HTTP_TIMEOUT`.
- Produces: `fetch_listings(portal, *, delay=1.5, log=...) -> Iterator[dict]` yielding each raw record augmented with `buyer_slug` and `status_code`; `_search_params(status, start, limit) -> dict` (pure, testable); `_STATUS_CODES`. Later tasks consume these.

- [ ] **Step 1: Write the failing tests**

Replace `test_gate_blocks_a_portal_without_permission` and `test_enabled_portal_has_no_capture_yet` in `scrapers/tests/test_agencies.py` (their premises change: `fetch_listings` now takes `portal` alone, and an enabled portal no longer raises `NotImplementedError`). Keep `test_no_portal_is_enabled_without_a_recorded_permission` untouched. New tests:

```python
def test_gate_blocks_a_portal_without_permission():
    import pytest as _pytest
    from toronto_bids.sources.bids_tenders import fetch_listings
    ungranted = {"slug": "example", "portal_url": "https://example.bidsandtenders.ca/",
                 "enabled": False, "permission": None}
    with _pytest.raises(PermissionError):
        next(fetch_listings(ungranted))          # generator: gate fires on first pull


def test_search_params_never_include_sort():
    from toronto_bids.sources.bids_tenders import _search_params
    p = _search_params(status=1, start=0, limit=50)
    assert "sort" not in p                        # sort= triggers a server error (verified)
    assert p == {"status": 1, "limit": 50, "start": 0, "dir": "desc", "from": "", "to": ""}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -k "gate_blocks or search_params" -v`
Expected: FAIL — `_search_params` undefined; `fetch_listings` still has the old two-arg signature.

- [ ] **Step 3: Replace the stub with the real source**

Write `scrapers/toronto_bids/sources/bids_tenders.py`:

```python
"""The bids&tenders portal source (#135) — listing metadata, PROVISIONAL parser.

Reads public bid listings from a body's bids&tenders portal via its plain-HTTP JSON grid
endpoint (no browser). GATED: fetch_listings raises PermissionError unless the portal's
config entry is enabled, which happens only when the body's written grant is recorded in
docs/permissions/ (the PMMD/Ariba precedent). Listings only — bid documents sit behind the
Vendor clickwrap and are never fetched.

PROVISIONAL: as of 2026-07-18 both permitted portals (TRCA, Zoo) are empty, so parse_listing
is mapped against the field names documented in the portal's own grid JS but has NOT been
validated against a real record. When a bid first appears, `tb enrich-agencies --portal
--record` captures real JSON to fixtures and the parser is completed and re-validated.
"""
import re
import time

import httpx

from toronto_bids import config
from toronto_bids.models import AgencySolicitation
from toronto_bids.store import db

_LANDING = "Module/Tenders/en"
_SEARCH = "Module/Tenders/en/Tender/Search/"
_NODE_RE = re.compile(r'id="NodeId"[^>]*value="([^"]+)"')
_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
# The status codes the endpoint accepts (Open / Awarded / Cancelled / ...). The exact
# code->label mapping is recorded when data first appears; the sweep covers them all.
_STATUS_CODES = range(0, 6)
_PAGE = 50
_DELAY = 1.5


def _search_params(status: int, start: int, limit: int = _PAGE) -> dict:
    # NEVER include 'sort' — 'sort=ClosingDate desc,Id' (space/comma) triggers a server error
    # that redirects to Error?aspxerrorpath (verified live 2026-07-18). Default order is fine.
    return {"status": status, "limit": limit, "start": start, "dir": "desc", "from": "", "to": ""}


def fetch_listings(portal: dict, *, delay: float = _DELAY, log=lambda _m: None):
    """Yield every listing record for a portal, across all statuses, paged and rate-limited.

    Manages its own httpx.Client (the antiforgery cookie set on the landing GET must persist
    to the search POSTs — a session concern specific to this source, not the shared HttpClient).
    Yields each raw JSON record with `buyer_slug` and `status_code` attached. On an empty portal
    (total=0, the current reality) this yields nothing — a clean no-op.
    """
    if not portal.get("enabled"):
        raise PermissionError(
            f"bids&tenders portal '{portal['slug']}' is not enabled: fetching requires the "
            f"body's written permission recorded in docs/permissions/ (see #135 / #103). "
            f"Current permission record: {portal.get('permission')!r}")
    base = portal["portal_url"].rstrip("/") + "/"
    client = httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "X-Requested-With": "XMLHttpRequest"},
        timeout=config.HTTP_TIMEOUT, follow_redirects=True)
    try:
        land = client.get(base + _LANDING).text
        node_m, tok_m = _NODE_RE.search(land), _TOKEN_RE.search(land)
        if not (node_m and tok_m):
            raise RuntimeError(f"bids&tenders {portal['slug']}: no NodeId/token on landing page")
        node, token = node_m.group(1), tok_m.group(1)   # FIRST token (bidDetail-scoped one 302s)
        for status in _STATUS_CODES:
            start = 0
            while True:
                time.sleep(delay)                        # rate-limit (permission condition)
                resp = client.post(base + _SEARCH + node,
                                   params=_search_params(status, start),
                                   data={"keywords": "", "__RequestVerificationToken": token})
                try:
                    payload = resp.json()
                except ValueError:
                    log(f"  {portal['slug']} status={status} start={start}: non-JSON, skipping")
                    break
                rows = payload.get("data") or []
                total = payload.get("total") or 0
                for rec in rows:
                    yield {**rec, "buyer_slug": portal["slug"], "status_code": status}
                start += len(rows)
                if not rows or start >= total:
                    break
    finally:
        client.close()
```

(The rest — `parse_listing`, `store_listings`, `record_listings` — is added in Tasks 2-4.)

- [ ] **Step 4: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v`
Expected: PASS (gate + `_search_params` + the untouched `test_no_portal_is_enabled_without_a_recorded_permission`). Then `uv run pytest` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/bids_tenders.py scrapers/tests/test_agencies.py
git commit -m "feat(portal): bids&tenders fetch_listings — verified session + paging, gated (#135)"
```

---

### Task 2: `parse_listing` (PROVISIONAL) + synthetic fixture

**Files:**
- Modify: `scrapers/toronto_bids/sources/bids_tenders.py` (append)
- Create: `scrapers/tests/fixtures/agencies/bids_tenders_record_sample.json`
- Modify: `scrapers/tests/fixtures/agencies/SOURCES.md` (note the synthetic fixture)
- Create: `scrapers/tests/test_bids_tenders.py`

**Interfaces:**
- Consumes: Task 1's module; `AgencySolicitation` model.
- Produces: `parse_listing(record: dict, buyer_id: int) -> AgencySolicitation`. Task 3 consumes it.

- [ ] **Step 1: Create the synthetic fixture**

`scrapers/tests/fixtures/agencies/bids_tenders_record_sample.json` — a hand-built record matching the JS-documented schema (`Id`, `Title`, closing date, counts) plus the `buyer_slug`/`status_code` that `fetch_listings` attaches. **Synthetic — the exact field names/formats are unverified until a real record is captured (#135).**

```json
{
  "Id": "0f9a1b2c-3d4e-5f60-7182-93a4b5c6d7e8",
  "ReferenceNumber": "RFT-2026-014",
  "Title": "Trail Bridge Replacement - Example Creek",
  "ClosingDate": "2026-08-15T14:00:00",
  "DatePosted": "2026-07-20T09:00:00",
  "StatusText": "Open",
  "Documents": 3,
  "Addendums": 0,
  "PlanTakers": 5,
  "buyer_slug": "trca",
  "status_code": 1
}
```

- [ ] **Step 2: Write the failing test**

`scrapers/tests/test_bids_tenders.py`:

```python
import json
import pathlib

from toronto_bids.sources.bids_tenders import parse_listing

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _sample():
    return json.loads((FIXTURES / "bids_tenders_record_sample.json").read_text())


def test_parse_listing_maps_documented_fields():
    row = parse_listing(_sample(), buyer_id=7)
    assert row.buyer_id == 7
    assert row.native_ref == "RFT-2026-014"          # ReferenceNumber, normalized
    assert row.title == "Trail Bridge Replacement - Example Creek"
    assert row.status == "Open"
    assert row.closing_date == "2026-08-15T14:00:00"
    assert row.posted_date == "2026-07-20T09:00:00"
    assert row.portal_url.endswith("/Tender/Detail/0f9a1b2c-3d4e-5f60-7182-93a4b5c6d7e8")
    assert row.source == "bids_tenders"


def test_parse_listing_falls_back_to_id_when_no_reference():
    rec = _sample(); del rec["ReferenceNumber"]
    row = parse_listing(rec, buyer_id=7)
    assert row.native_ref == "0F9A1B2C-3D4E-5F60-7182-93A4B5C6D7E8"   # Id, uppercased
```

- [ ] **Step 3: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -v`
Expected: FAIL — `cannot import name 'parse_listing'`.

- [ ] **Step 4: Implement `parse_listing`**

Append to `bids_tenders.py`:

```python
# Portal base per slug, for building a record's detail URL. Derived from config so the two
# stay in sync.
_PORTAL_BASE = {p["slug"]: p["portal_url"].rstrip("/") for p in config.BIDS_TENDERS_PORTALS}

_WS = re.compile(r"\s+")


def _native_ref(record: dict) -> str:
    """The body's own bid identifier, normalized like the board-report path (trim/upper/
    collapse-ws) so a portal row and a board-report row for one procurement share a key."""
    raw = record.get("ReferenceNumber") or record.get("BidNumber") or record.get("Id") or ""
    return _WS.sub(" ", str(raw)).strip().upper()


def parse_listing(record: dict, buyer_id: int) -> AgencySolicitation:
    """Map one raw portal record to an AgencySolicitation. PROVISIONAL (see module docstring):
    field names/formats are from the grid JS, unverified until a real record is captured."""
    base = _PORTAL_BASE.get(record.get("buyer_slug"), "")
    rid = record.get("Id")
    portal_url = f"{base}/Module/Tenders/en/Tender/Detail/{rid}" if (base and rid) else None
    return AgencySolicitation(
        buyer_id=buyer_id,
        native_ref=_native_ref(record),
        title=record.get("Title"),
        status=record.get("StatusText") or record.get("Status"),
        posted_date=record.get("DatePosted") or record.get("PostDate"),
        closing_date=record.get("ClosingDate") or record.get("BidClosingDate"),
        portal_url=portal_url,
        source="bids_tenders",
    )
```

- [ ] **Step 5: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -v`
Expected: PASS.

- [ ] **Step 6: Note the synthetic fixture in SOURCES.md**

Append to `scrapers/tests/fixtures/agencies/SOURCES.md`:

```markdown
## bids_tenders_record_sample.json — SYNTHETIC (#135)

Hand-built, NOT a real capture. As of 2026-07-18 both permitted bids&tenders portals (TRCA,
Zoo) are empty (total=0, all statuses), so no real listing record exists to record. This
fixture matches the field names documented in the portal's grid JS
(Module/Tenders/Resources/scriptsV2/home/index.js: Id, Title, ClosingDate, Documents,
Addendums, PlanTakers) and exercises parse_listing's mapping mechanics only. `parse_listing`
is PROVISIONAL until `tb enrich-agencies --portal --record` captures a real record and replaces
this fixture (#135 deferred item).
```

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/sources/bids_tenders.py scrapers/tests/test_bids_tenders.py \
        scrapers/tests/fixtures/agencies/bids_tenders_record_sample.json \
        scrapers/tests/fixtures/agencies/SOURCES.md
git commit -m "feat(portal): provisional parse_listing + synthetic fixture (#135)"
```

---

### Task 3: `store_listings` — COALESCE upsert into agency_solicitation

**Files:**
- Modify: `scrapers/toronto_bids/sources/bids_tenders.py` (append)
- Modify: `scrapers/tests/test_bids_tenders.py` (append)

**Interfaces:**
- Consumes: `parse_listing` (Task 2), `db.upsert_row`, `AgencySolicitation`, `buyers.seed_buyers`.
- Produces: `store_listings(conn, records, buyer_ids) -> int` where `buyer_ids` is the `{slug: id}` map from `seed_buyers`. Returns the number of rows upserted.

- [ ] **Step 1: Write the failing tests**

Append to `scrapers/tests/test_bids_tenders.py`:

```python
import sqlite3

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencySolicitation
from toronto_bids.sources.bids_tenders import store_listings
from toronto_bids.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_store_listings_inserts_portal_row(conn):
    ids = seed_buyers(conn)
    n = store_listings(conn, [_sample()], ids)
    assert n == 1
    row = conn.execute("SELECT native_ref, title, status, source FROM agency_solicitation").fetchone()
    assert row["native_ref"] == "RFT-2026-014"
    assert row["source"] == "bids_tenders"


def test_store_listings_enriches_a_board_report_row(conn):
    ids = seed_buyers(conn)
    # A board-report row already exists for the same ref, with a title but no dates/status.
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="RFT-2026-014", title="Board title",
        status="awarded", source="trca_board"), overwrite=False)
    store_listings(conn, [_sample()], ids)
    rows = conn.execute("SELECT title, status, closing_date FROM agency_solicitation "
                        "WHERE native_ref='RFT-2026-014'").fetchall()
    assert len(rows) == 1                              # COALESCE-enriched, not duplicated
    assert rows[0]["title"] == "Board title"           # board title preserved (overwrite guard)
    assert rows[0]["closing_date"] == "2026-08-15T14:00:00"  # portal filled the empty date


def test_store_listings_empty_is_noop(conn):
    ids = seed_buyers(conn)
    assert store_listings(conn, [], ids) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -k store -v`
Expected: FAIL — `cannot import name 'store_listings'`.

- [ ] **Step 3: Implement `store_listings`**

Append to `bids_tenders.py`:

```python
def store_listings(conn, records, buyer_ids: dict) -> int:
    """Upsert each parsed listing into agency_solicitation. overwrite=True: the portal owns the
    listing fields (status/dates), so a nightly re-fetch keeps an open bid current, while
    COALESCE still protects a board-report-supplied title from being nulled. Skips a record
    whose buyer_slug is not a seeded buyer. Returns rows upserted."""
    n = 0
    for record in records:
        buyer_id = buyer_ids.get(record.get("buyer_slug"))
        if buyer_id is None:
            continue
        db.upsert_row(conn, parse_listing(record, buyer_id), overwrite=True)
        n += 1
    conn.commit()
    return n
```

- [ ] **Step 4: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -v` then `uv run pytest`
Expected: all PASS. (Note the `overwrite=True` + COALESCE: `db._upsert_keyed` fills the NULL `closing_date` but keeps the non-NULL board title — verify the enrich test proves both.)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/bids_tenders.py scrapers/tests/test_bids_tenders.py
git commit -m "feat(portal): store_listings — COALESCE upsert into agency_solicitation (#135)"
```

---

### Task 4: `record_listings` + `tb enrich-agencies --portal [--record]`

**Files:**
- Modify: `scrapers/toronto_bids/sources/bids_tenders.py` (append `record_listings` + `run_portal_capture`)
- Modify: `scrapers/toronto_bids/config.py` (append `PORTAL_RECORDINGS_DIR`)
- Modify: `scrapers/toronto_bids/cli.py` (`--portal`/`--record` flags + wiring in `_cmd_enrich_agencies`)
- Modify: `scrapers/tests/test_bids_tenders.py` (append)

**Interfaces:**
- Consumes: `fetch_listings`, `store_listings`, `seed_buyers`, `config.BIDS_TENDERS_PORTALS`.
- Produces: `record_listings(records, out_dir) -> int`; `run_portal_capture(conn, *, record=False, only=None, log=...) -> dict` (per-body isolated orchestrator returning `{slug: count}` and never raising for one body's failure). `run_portal_capture` is what nightly (Task 5) calls.

- [ ] **Step 1: Write the failing tests**

Append to `scrapers/tests/test_bids_tenders.py`:

```python
def test_record_listings_writes_one_file_per_record(tmp_path):
    from toronto_bids.sources.bids_tenders import record_listings
    recs = [dict(_sample(), status_code=1), dict(_sample(), status_code=3)]
    n = record_listings(recs, tmp_path)
    assert n == 2
    written = sorted(p.name for p in tmp_path.glob("*.json"))
    assert all(name.startswith("trca-") for name in written)


def test_run_portal_capture_isolates_a_failing_body(conn, monkeypatch):
    from toronto_bids.sources import bids_tenders as bt
    ids = seed_buyers(conn)

    def fake_fetch(portal, **_kw):
        if portal["slug"] == "trca":
            raise RuntimeError("boom")            # one body fails
        yield dict(_sample(), buyer_slug="toronto-zoo")

    monkeypatch.setattr(bt, "fetch_listings", fake_fetch)
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    assert result["toronto-zoo"] == 1             # zoo still captured
    assert "trca" in result and result["trca"] == "FAILED: boom"   # trca isolated, recorded
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -k "record_listings or portal_capture" -v`
Expected: FAIL — names undefined.

- [ ] **Step 3: Implement**

Append to `config.py`:

```python
# Raw bids&tenders listing JSON captured by `--record`, one file per record — the seed for
# real parser fixtures once a portal has data (#135).
PORTAL_RECORDINGS_DIR = DATA_DIR / "agencies" / "portal_recordings"
```

Append to `bids_tenders.py`:

```python
import json

from toronto_bids.buyers import seed_buyers


def record_listings(records, out_dir) -> int:
    """Write each raw record to out_dir/<slug>-<status>-<n>.json — the seed for real fixtures
    once a portal has data. Returns the count written."""
    import pathlib
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for record in records:
        slug = record.get("buyer_slug", "unknown")
        status = record.get("status_code", "x")
        (out / f"{slug}-{status}-{n}.json").write_text(json.dumps(record, indent=1, default=str))
        n += 1
    return n


def run_portal_capture(conn, *, record: bool = False, only=None, log=lambda _m: None) -> dict:
    """Fetch + store (and optionally record) every enabled portal, per-body isolated: one
    body's failure is caught and reported, never stops the others. Returns {slug: count | 'FAILED: ...'}."""
    buyer_ids = seed_buyers(conn)
    result = {}
    for portal in config.BIDS_TENDERS_PORTALS:
        if not portal["enabled"]:
            continue
        if only and portal["slug"] not in only:
            continue
        try:
            records = list(fetch_listings(portal, log=log))
            if record:
                written = record_listings(records, config.PORTAL_RECORDINGS_DIR)
                log(f"  {portal['slug']}: recorded {written} raw record(s)")
            result[portal["slug"]] = store_listings(conn, records, buyer_ids)
            log(f"  {portal['slug']}: {result[portal['slug']]} listing(s) stored")
        except Exception as exc:                       # per-body isolation (empty portal is fine; a real error is caught)
            result[portal["slug"]] = f"FAILED: {exc}"
            log(f"  FAILED {portal['slug']}: {exc}")
    return result
```

Wire the CLI. In `cli.py`'s `build_parser`, add to the `enrich-agencies` subparser (`p_ag`):

```python
    p_ag.add_argument("--portal", action="store_true",
                      help="Capture bids&tenders portal listings for enabled+permitted bodies "
                           "(plain HTTP, rate-limited). Currently a no-op while portals are empty.")
    p_ag.add_argument("--record", action="store_true",
                      help="With --portal: also dump each raw JSON record under "
                           "<DATA_DIR>/agencies/portal_recordings/ to seed parser fixtures.")
```

In `_cmd_enrich_agencies`, after the existing trca/zoo blocks and before `build_supplier_dimension`, add:

```python
        if args.portal:
            try:
                from toronto_bids.sources.bids_tenders import run_portal_capture
                only = None if not args.only else {"trca": "trca", "zoo": "toronto-zoo"}[args.only]
                res = run_portal_capture(conn, record=args.record,
                                         only={only} if only else None, log=out)
                print(f"  portal listings      : {res}")
                for slug, v in res.items():
                    if isinstance(v, str) and v.startswith("FAILED"):
                        failures.append((f"portal:{slug}", v))
            except Exception as exc:
                failures.append(("portal", str(exc)))
```

- [ ] **Step 4: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_bids_tenders.py -v` then `uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/bids_tenders.py scrapers/toronto_bids/config.py \
        scrapers/toronto_bids/cli.py scrapers/tests/test_bids_tenders.py
git commit -m "feat(portal): --record mode + run_portal_capture orchestrator + CLI wiring (#135)"
```

---

### Task 5: nightly integration + CLAUDE.md

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (`_cmd_nightly` — add an isolated portal step)
- Modify: `CLAUDE.md` (extend the agency-capture subsection)
- Modify: `scrapers/tests/test_bids_tenders.py` (append a nightly-isolation assertion via `run_portal_capture`, already covered — add a doc-smoke instead)

**Interfaces:**
- Consumes: `run_portal_capture` (Task 4).

- [ ] **Step 1: Add the nightly step**

In `cli.py`'s `_cmd_nightly`, inside the `if conn is not None:` block, after the `award_summary` step and before `export`, add an isolated portal step mirroring the existing pattern:

```python
        try:
            from toronto_bids.sources.bids_tenders import run_portal_capture
            res = run_portal_capture(conn, log=out)
            for slug, v in res.items():
                if isinstance(v, str) and v.startswith("FAILED"):
                    failures.append((f"portal:{slug}", v))
        except Exception as exc:
            failures.append(("portal", str(exc)))
```

(Isolated exactly like `sync` and `award_summary`: a portal failure records to `failures` and never stops export. On empty portals it stores 0 and adds nothing.)

- [ ] **Step 2: Verify nightly still composes**

Run: `cd scrapers && uv run pytest tests/test_nightly.py -v` (if present) then `uv run pytest`
Expected: PASS — the new step is caught like the others; existing nightly tests unaffected. If `test_nightly.py` monkeypatches steps, confirm the portal step's import doesn't break the patched path; if it does, guard the import as the other in-function imports are.

- [ ] **Step 3: Update CLAUDE.md**

In the "Agency capture" subsection, append:

```markdown
The bids&tenders **portal listings** (`sources/bids_tenders.py`, #135) are captured over plain
HTTP — the grid loads from `POST /Module/Tenders/en/Tender/Search/<NodeId>` (session cookie +
the FIRST antiforgery token; **never send `sort=` — it errors**), no browser. `fetch_listings`
is gated (`PermissionError` until a body's grant is recorded in `docs/permissions/`); TRCA and
the Zoo granted 2026-07-18. Rows land in `agency_solicitation` (`overwrite=True`, COALESCE-
enriching any board-report row with the same `native_ref`). Runs in `tb nightly` (isolated) and
`tb enrich-agencies --portal`. **Both portals are currently empty (total=0), so parse_listing is
PROVISIONAL** — mapped to the grid JS's field names, validated only against a synthetic fixture;
`--record` dumps raw JSON to seed a real fixture when a bid first appears, at which point the
parser (and a possible portal `agency_award` path) is completed. No bid documents — Vendor clickwrap.
```

- [ ] **Step 4: Full suite + commit**

Run: `cd scrapers && uv run pytest`
Expected: all PASS.

```bash
git add scrapers/toronto_bids/cli.py CLAUDE.md scrapers/tests/test_bids_tenders.py
git commit -m "feat(portal): isolated nightly portal step; document the capture (#135)"
```

---

## After the last task

Run the full suite once more. Then the real-world verification: `cd scrapers && uv run tb enrich-agencies --portal --record` — expected today to fetch, store **0 rows**, record **0 files**, exit 0 (the honest verification while portals are empty). Use superpowers:finishing-a-development-branch. **#135 stays open** for the deferred parser-validation task (triggered when a portal first has data); note that on the issue. #109/#132 already addressed by the board-report capture.
