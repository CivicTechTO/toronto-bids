# Toronto Bids Scraper — P2: Ariba Discovery JSON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive currently-open City-of-Toronto SAP Ariba Discovery postings (which vanish when they close) into a new `ariba_posting` table, bridging each to its `document_number` where possible — using only public JSON APIs, no auth, no browser.

**Architecture:** A new source adapter (`AribaDiscoverySource`) follows the established `fetch() → normalize()` pattern. `fetch()` does all network I/O: it POSTs the public `doIndexedSearch` feed, keeps the City-of-Toronto postings, then GETs the `/rfx/{rfxID}` detail for each — wrapping each detail call in its own try/except so a failing posting yields `detail=None` and is still archived from its search metadata. `normalize()` is pure: it builds `AribaPosting` rows and bridges `rfxID → document_number` via the detail's `externalRfxId` (with a title-embedded `Doc##########` fallback). Idempotent upserts with `overwrite=True` mean a later successful run fills gaps left by a transient 500 without wiping an earlier snapshot.

**Tech Stack:** Python 3.12+, `uv`, `httpx` (existing `HttpClient` with retry-on-500), SQLite, `pytest`. No browser, no auth in this scope.

## Global Constraints

- **Python 3.12+**, managed with **uv** only (`uv add`, `uv run`). All `uv`/`pytest` commands run from inside `scrapers/`. Package import name `toronto_bids`.
- **Builds on the merged P0/P1 package.** Reuse, do not duplicate: `models.py` dataclasses, `config.py`, `http.HttpClient` (has `get_json(url, params=None)`, `post_json(url, json=None, params=None)`, retries on 5xx via `config.HTTP_RETRIES`), `store/db.py` (`upsert_row(conn, row, *, overwrite)`, `_upsert_keyed`, `counts`, `init_db`), `store/schema.sql`, `sources/base.Source` protocol (`name`, `overwrite`, `fetch`, `normalize`), `pipeline.py` (`default_sources`, `run_source`, `sync`), `linking/document_number.normalize_document_number`.
- **Bridge is LEAN and reliable-only:** `rfxID → document_number` comes from (1) the detail's `externalRfxId` run through `normalize_document_number`, and (2) a `Doc(\d{10})` regex on the title. **No OData `Ariba_Discovery_Posting_Link` fallback** (verified live: the search/detail rfxID namespace `1110xxxxxx` and OData's link-id namespace do not overlap — 0/42 match). **No fuzzy title→solicitation matching** (risks wrong bridges). An un-bridged posting is still archived.
- **Archive always, never delete.** Every City-of-Toronto posting produces exactly one `ariba_posting` row (bridged or not). `raw_json` holds the detail JSON snapshot, or `NULL` when the detail call failed. `first_seen`/`last_seen` (TEXT, `datetime('now')`) give archive timing; upserts touch `last_seen`.
- **`AribaDiscoverySource.overwrite = True`.** With the store's `overwrite=True` COALESCE (`COALESCE(excluded.col, table.col)`), a later successful detail fills columns that were NULL, and a later 500 (all-NULL detail fields) does **not** wipe an earlier captured snapshot. This is the archival guarantee.
- **Per-posting isolation inside `fetch()`:** a detail-call exception yields `detail=None` and continues; it must never abort the source. (The pipeline's per-source isolation is already in place separately.)
- **Endpoints (verified live 2026-07-15):**
  - Search: `POST https://service.ariba.com/Network/discoveryweb/search/public/v1/doIndexedSearch` with query `?siteName=Quote` and JSON body `{"pageSize":1000,"pageNum":0,"searchType":"Quote","sortBy":"RESPONSE_DEAD_LINE","filters":[]}`. Returns `{"totalNumberOfRecords":N, "solarRecords":[...]}`. ~835 global records, **42 with `customerName == "City of Toronto"`**. Single page suffices (total < pageSize).
  - Detail: `GET https://service.ariba.com/Network/discoveryweb/api/public/v1/rfx/{rfxID}` with header `Accept: application/json`. ~60% return 200; **~40% return HTTP 500 persistently** on any given run (transient across runs). 200 body carries `externalRfxId` (e.g. `"Doc5672751291"`), `categories`, `territories`, `opportunityAmount`, `status`, `publicPostingUrl`, `sourcingUrl`.
- **Filter to City of Toronto:** keep only search records where `customerName == "City of Toronto"`.

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` (§2.1 sources #6/#7, §3.2 bridge joins, §5 `ariba_posting`, §10 P2).

---

## File Structure

New and modified files (all under `scrapers/`):

```
scrapers/
  toronto_bids/
    models.py                     # MODIFY: add AribaPosting dataclass
    config.py                     # MODIFY: add Ariba search/detail URLs, body, params, customer name
    http.py                       # MODIFY: add optional headers= to get_json/post_json
    pipeline.py                   # MODIFY: add AribaDiscoverySource to default_sources
    store/
      schema.sql                  # MODIFY: add ariba_posting table + index
      db.py                       # MODIFY: _ARIBA_POSTING_COLS, upsert_row branch, counts()
    linking/
      document_number.py          # MODIFY: add bridge_document_number(external_rfx_id, title)
    sources/
      ariba.py                    # CREATE: AribaDiscoverySource (fetch + normalize) + helpers
  tests/
    fixtures/
      ariba_search_record.json    # CREATE: one real City-of-Toronto search record
      ariba_detail.json           # CREATE: its real /rfx detail response (trimmed)
    test_document_number.py       # MODIFY: add bridge_document_number tests
    test_db.py                    # MODIFY: add ariba_posting upsert/counts tests
    test_http.py                  # MODIFY: add headers pass-through test
    test_ariba.py                 # CREATE: fetch (search+detail+500 isolation) + normalize tests
    test_pipeline_integration.py  # MODIFY: add ariba fetch→normalize→upsert integration test
```

---

### Task 1: `AribaPosting` model + `ariba_posting` table + store wiring

**Files:**
- Modify: `scrapers/toronto_bids/models.py`
- Modify: `scrapers/toronto_bids/store/schema.sql`
- Modify: `scrapers/toronto_bids/store/db.py`
- Modify: `scrapers/tests/test_db.py`

**Interfaces:**
- Consumes: `db.upsert_row(conn, row, *, overwrite)`, `db.counts(conn)`.
- Produces:
  - `models.AribaPosting` (frozen dataclass; fields below, `rfx_id` required, rest default `None`, `source: str = ""`).
  - `ariba_posting` table keyed on `rfx_id`.
  - `db.upsert_row` dispatches `AribaPosting` to the `ariba_posting` table (conflict key `rfx_id`).
  - `db.counts(conn)` includes `"ariba_posting"`.

- [ ] **Step 1: Add the `AribaPosting` dataclass**

Append to `scrapers/toronto_bids/models.py`:

```python
@dataclass(frozen=True)
class AribaPosting:
    rfx_id: str
    document_number: str | None = None
    title: str | None = None
    posting_type: str | None = None      # detail 'type' field — unreliable (often "RFI")
    status: str | None = None
    customer_name: str | None = None
    posted_date: str | None = None
    close_date: str | None = None
    categories: str | None = None        # JSON array of category names
    amount_min: str | None = None
    amount_max: str | None = None
    currency: str | None = None
    public_posting_url: str | None = None
    sourcing_url: str | None = None      # authenticated event URL (for a later attachments phase)
    external_rfx_id: str | None = None   # raw e.g. "Doc5672751291"
    raw_json: str | None = None          # detail JSON snapshot, or None if the detail call failed
    source: str = ""
```

- [ ] **Step 2: Add the `ariba_posting` table**

Append to `scrapers/toronto_bids/store/schema.sql`:

```sql

-- ariba_posting archives open SAP Ariba Discovery postings (which disappear when they close).
-- overwrite=True upserts fill NULL columns on later runs; a later 500 (all-NULL) never wipes an
-- earlier captured snapshot. document_number is bridged best-effort and may be NULL.
CREATE TABLE IF NOT EXISTS ariba_posting (
    rfx_id              TEXT PRIMARY KEY,
    document_number     TEXT,
    title               TEXT,
    posting_type        TEXT,
    status              TEXT,
    customer_name       TEXT,
    posted_date         TEXT,
    close_date          TEXT,
    categories          TEXT,
    amount_min          TEXT,
    amount_max          TEXT,
    currency            TEXT,
    public_posting_url  TEXT,
    sourcing_url        TEXT,
    external_rfx_id     TEXT,
    raw_json            TEXT,
    source              TEXT,
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ariba_posting_docnum ON ariba_posting (document_number);
```

- [ ] **Step 3: Write the failing store tests**

Add to `scrapers/tests/test_db.py` (the file already imports from `toronto_bids.models` and `toronto_bids.store`; add `AribaPosting` to the models import):

```python
def test_upsert_ariba_posting_is_idempotent(conn):
    from toronto_bids.models import AribaPosting
    p = AribaPosting(rfx_id="1110015885", document_number="5672751291",
                     title="RFT Watermain", raw_json="{}", source="ariba_discovery")
    db.upsert_row(conn, p, overwrite=True)
    db.upsert_row(conn, p, overwrite=True)
    assert db.counts(conn)["ariba_posting"] == 1


def test_ariba_posting_later_500_does_not_wipe_snapshot(conn):
    from toronto_bids.models import AribaPosting
    # Run 1: detail succeeded -> raw_json + document_number captured.
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number="5672751291",
                                     raw_json="{\"x\":1}", source="ariba_discovery"), overwrite=True)
    # Run 2: detail 500'd -> those fields arrive as None. overwrite=True must NOT clobber them.
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number=None,
                                     raw_json=None, source="ariba_discovery"), overwrite=True)
    row = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert row["document_number"] == "5672751291"
    assert row["raw_json"] == "{\"x\":1}"


def test_counts_includes_ariba_posting(conn):
    assert "ariba_posting" in db.counts(conn)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_db.py -k ariba -v`
Expected: FAIL — `AribaPosting` not importable / `counts` has no `ariba_posting` key.

- [ ] **Step 5: Wire the store**

In `scrapers/toronto_bids/store/db.py`:

Add `AribaPosting` to the models import:
```python
from toronto_bids.models import Award, NonCompetitive, Solicitation, AribaPosting
```

Add the column list after `_NONCOMP_COLS`:
```python
_ARIBA_POSTING_COLS = [
    "rfx_id", "document_number", "title", "posting_type", "status", "customer_name",
    "posted_date", "close_date", "categories", "amount_min", "amount_max", "currency",
    "public_posting_url", "sourcing_url", "external_rfx_id", "raw_json", "source",
]
```

Add a branch in `upsert_row` (before the final `else`):
```python
    elif isinstance(row, AribaPosting):
        values = [getattr(row, c) for c in _ARIBA_POSTING_COLS]
        _upsert_keyed(conn, "ariba_posting", _ARIBA_POSTING_COLS, values,
                      ["rfx_id"], overwrite)
```

Add `"ariba_posting"` to the `counts` table list:
```python
def counts(conn) -> dict:
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting", "sync_run"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: all PASS (existing + 3 new).

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/models.py scrapers/toronto_bids/store scrapers/tests/test_db.py
git commit -m "feat(scraper): add ariba_posting table, model, and store wiring"
```

---

### Task 2: `bridge_document_number` helper

The pure rfxID→document_number bridge: try the detail's `externalRfxId`, then a title-embedded `Doc##########`.

**Files:**
- Modify: `scrapers/toronto_bids/linking/document_number.py`
- Modify: `scrapers/tests/test_document_number.py`

**Interfaces:**
- Consumes: `normalize_document_number(raw) -> str | None` (same module).
- Produces: `bridge_document_number(external_rfx_id: str | None, title: str | None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Add to `scrapers/tests/test_document_number.py`:

```python
from toronto_bids.linking.document_number import bridge_document_number


def test_bridge_uses_external_rfx_id():
    # "Doc5672751291" -> strip -> "5672751291"
    assert bridge_document_number("Doc5672751291", "some title") == "5672751291"


def test_bridge_falls_back_to_title_embedded_doc():
    title = "Doc5581608073 - Request for Quotations for the non-exclusive supply"
    assert bridge_document_number(None, title) == "5581608073"


def test_bridge_prefers_external_rfx_id_over_title():
    assert bridge_document_number("Doc5672751291", "Doc9999999999 - other") == "5672751291"


def test_bridge_returns_none_when_neither_resolves():
    assert bridge_document_number(None, "Request for Tenders for Road Resurfacing") is None
    assert bridge_document_number("", "") is None
    assert bridge_document_number(None, None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_document_number.py -k bridge -v`
Expected: FAIL — `bridge_document_number` not defined.

- [ ] **Step 3: Implement the helper**

Add to `scrapers/toronto_bids/linking/document_number.py` (after `normalize_document_number`):

```python
_TITLE_DOC = re.compile(r"Doc(\d{10})")


def bridge_document_number(external_rfx_id: str | None, title: str | None) -> str | None:
    """Bridge an Ariba posting to its 10-digit document_number.

    Primary: the detail endpoint's externalRfxId (e.g. "Doc5672751291").
    Fallback: a "Doc##########" token embedded in the posting title.
    Returns None if neither yields a valid document number.
    """
    doc = normalize_document_number(external_rfx_id)
    if doc is not None:
        return doc
    if title:
        match = _TITLE_DOC.search(title)
        if match:
            return normalize_document_number(match.group(1))
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_document_number.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/linking/document_number.py scrapers/tests/test_document_number.py
git commit -m "feat(scraper): add rfxID->document_number bridge helper"
```

---

### Task 3: HTTP header support + config + Ariba `fetch()`

Add optional headers to the HTTP client, the Ariba endpoint config, and the source's network half (search + per-posting detail with 500 isolation).

**Files:**
- Modify: `scrapers/toronto_bids/http.py`
- Modify: `scrapers/toronto_bids/config.py`
- Create: `scrapers/toronto_bids/sources/ariba.py`
- Modify: `scrapers/tests/test_http.py`
- Create: `scrapers/tests/test_ariba.py`

**Interfaces:**
- Consumes: `HttpClient.post_json`, `HttpClient.get_json`, `config.*`.
- Produces:
  - `HttpClient.get_json(url, params=None, headers=None)` and `HttpClient.post_json(url, json=None, params=None, headers=None)`.
  - `config.ARIBA_SEARCH_URL`, `config.ARIBA_DETAIL_URL`, `config.ARIBA_SEARCH_BODY`, `config.ARIBA_SEARCH_PARAMS`, `config.ARIBA_CUSTOMER_NAME`.
  - `ariba.AribaDiscoverySource` with `name="ariba_discovery"`, `overwrite=True`, `fetch(http) -> Iterable[dict]` yielding `{"search": <record>, "detail": <dict|None>}`. (`normalize` is added in Task 4.)

- [ ] **Step 1: Add `headers` pass-through to the HTTP client**

In `scrapers/toronto_bids/http.py`, thread an optional `headers` through `_request` and both public methods:

```python
    def _request(self, method, url, headers=None, **kwargs):
        last_exc = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < self._retries:
                time.sleep(self._backoff * (2 ** attempt))
        raise last_exc

    def get_json(self, url, params=None, headers=None):
        return self._request("GET", url, params=params, headers=headers).json()

    def post_json(self, url, json=None, params=None, headers=None):
        return self._request("POST", url, json=json, params=params, headers=headers).json()
```

- [ ] **Step 2: Write the failing headers test**

Add to `scrapers/tests/test_http.py`:

```python
def test_get_json_sends_custom_headers():
    seen = {}
    def handler(request):
        seen["accept"] = request.headers.get("Accept")
        return httpx.Response(200, json={"ok": True})
    client = _client(handler)
    client.get_json("https://example.test/x", headers={"Accept": "application/json"})
    assert seen["accept"] == "application/json"
```

(The `_client` helper already exists at the top of `test_http.py`.)

- [ ] **Step 3: Run the headers test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_http.py -k custom_headers -v`
Expected: FAIL — `get_json()` got an unexpected keyword argument `headers` (before edit) or assertion error.

Note: apply Step 1 to make it pass. Run: `cd scrapers && uv run pytest tests/test_http.py -v` → all PASS.

- [ ] **Step 4: Add Ariba config**

Append to `scrapers/toronto_bids/config.py`:

```python
# SAP Ariba Discovery public JSON APIs (no auth).
ARIBA_SEARCH_URL = "https://service.ariba.com/Network/discoveryweb/search/public/v1/doIndexedSearch"
ARIBA_DETAIL_URL = "https://service.ariba.com/Network/discoveryweb/api/public/v1/rfx/{rfx_id}"
ARIBA_SEARCH_PARAMS = {"siteName": "Quote"}
ARIBA_SEARCH_BODY = {
    "pageSize": 1000,
    "pageNum": 0,
    "searchType": "Quote",
    "sortBy": "RESPONSE_DEAD_LINE",
    "filters": [],
}
ARIBA_CUSTOMER_NAME = "City of Toronto"
```

- [ ] **Step 5: Write the failing `fetch` tests**

Create `scrapers/tests/test_ariba.py`:

```python
import httpx

from toronto_bids.http import HttpClient
from toronto_bids.sources.ariba import AribaDiscoverySource


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


SEARCH_BODY = {
    "totalNumberOfRecords": 3,
    "solarRecords": [
        {"rfxID": "1110015885", "customerName": "City of Toronto", "title": "RFT Watermain"},
        {"rfxID": "1110099999", "customerName": "City of Toronto", "title": "RFQ Widgets"},
        {"rfxID": "1110000001", "customerName": "TransLink", "title": "Other buyer"},
    ],
}


def test_fetch_keeps_only_toronto_and_pairs_detail():
    def handler(request):
        if "doIndexedSearch" in str(request.url):
            return httpx.Response(200, json=SEARCH_BODY)
        # detail: 1110015885 succeeds, 1110099999 500s persistently
        if request.url.path.endswith("1110015885"):
            return httpx.Response(200, json={"id": "1110015885", "externalRfxId": "Doc5672751291"})
        return httpx.Response(500, text="boom")
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    # TransLink record dropped; two Toronto records kept.
    assert len(raws) == 2
    by_id = {r["search"]["rfxID"]: r for r in raws}
    assert by_id["1110015885"]["detail"]["externalRfxId"] == "Doc5672751291"
    # The 500'd posting is still yielded, with detail=None (per-posting isolation).
    assert by_id["1110099999"]["detail"] is None


def test_fetch_does_not_raise_when_all_details_fail():
    def handler(request):
        if "doIndexedSearch" in str(request.url):
            return httpx.Response(200, json=SEARCH_BODY)
        return httpx.Response(500, text="boom")
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    assert len(raws) == 2
    assert all(r["detail"] is None for r in raws)


def test_source_attributes():
    src = AribaDiscoverySource()
    assert src.name == "ariba_discovery"
    assert src.overwrite is True
```

- [ ] **Step 6: Run the fetch tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_ariba.py -v`
Expected: FAIL — `toronto_bids.sources.ariba` not importable.

- [ ] **Step 7: Implement `fetch` (normalize comes in Task 4)**

Create `scrapers/toronto_bids/sources/ariba.py`:

```python
from typing import Iterable

from toronto_bids import config
from toronto_bids.sources.base import Row


class AribaDiscoverySource:
    """Archives open City-of-Toronto SAP Ariba Discovery postings via public JSON APIs."""

    name = "ariba_discovery"
    overwrite = True  # later successful detail fills NULLs; a later 500 never wipes a snapshot.

    def fetch(self, http) -> Iterable[dict]:
        data = http.post_json(
            config.ARIBA_SEARCH_URL,
            json=config.ARIBA_SEARCH_BODY,
            params=config.ARIBA_SEARCH_PARAMS,
        )
        for record in data.get("solarRecords", []):
            if record.get("customerName") != config.ARIBA_CUSTOMER_NAME:
                continue
            detail = None
            try:
                detail = http.get_json(
                    config.ARIBA_DETAIL_URL.format(rfx_id=record["rfxID"]),
                    headers={"Accept": "application/json"},
                )
            except Exception:
                # Per-posting isolation: ~40% of details 500. Archive the search
                # metadata anyway; a later run's detail call fills the gap.
                detail = None
            yield {"search": record, "detail": detail}

    def normalize(self, raw: dict) -> Iterable[Row]:  # implemented in Task 4
        raise NotImplementedError
```

Note: `normalize` is intentionally a stub here and is completed in Task 4; it is not exercised by this task's tests.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_ariba.py tests/test_http.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add scrapers/toronto_bids/http.py scrapers/toronto_bids/config.py scrapers/toronto_bids/sources/ariba.py scrapers/tests/test_http.py scrapers/tests/test_ariba.py
git commit -m "feat(scraper): add Ariba Discovery search+detail fetch with per-posting 500 isolation"
```

---

### Task 4: Ariba `normalize()` + bridge + fixtures

Turn each `{"search", "detail"}` raw into one `AribaPosting`, bridging the document number and snapshotting the detail JSON.

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba.py` (implement `normalize` + a module `normalize_posting`)
- Create: `scrapers/tests/fixtures/ariba_search_record.json`
- Create: `scrapers/tests/fixtures/ariba_detail.json`
- Modify: `scrapers/tests/test_ariba.py`

**Interfaces:**
- Consumes: `bridge_document_number`, `models.AribaPosting`.
- Produces: `ariba.normalize_posting(raw: dict) -> Iterable[AribaPosting]`; `AribaDiscoverySource.normalize` delegates to it.

- [ ] **Step 1: Create the real fixtures**

`scrapers/tests/fixtures/ariba_search_record.json` (one real City-of-Toronto search record):

```json
{
  "productsAndServicesCategories": [
    "Sidewalk construction and repair service",
    "Sewer line construction service"
  ],
  "shipToOrServiceLocations": ["Toronto (Mississauga) - Ontario"],
  "title": "Request for Tenders for Watermain and Sewer Replacement on various roads",
  "rfxID": "1110015885",
  "minAmount": "712.5552230298",
  "maxAmount": "70542967.0799487",
  "customerName": "City of Toronto",
  "rfxType": "RFI",
  "datePosted": "2026-06-16T12:16:17-07:00",
  "endDate": "2026-07-17T09:00:00-07:00"
}
```

`scrapers/tests/fixtures/ariba_detail.json` (its real `/rfx` detail, trimmed):

```json
{
  "id": "1110015885",
  "type": "RFI",
  "title": "Request for Tenders for Watermain and Sewer Replacement on various roads",
  "status": "PUBLISHED",
  "startDate": "2026-06-16T12:16:17",
  "endDate": "2026-07-17T09:00:00",
  "externalRfxId": "Doc5672751291",
  "publicPostingUrl": "https://discovery.ariba.com/rfx/1110015885",
  "sourcingUrl": "https://s1.ariba.com/Sourcing/Main/ad/gotoEvent/NetworkRFQDirectAction?rfxId=Doc5672751291&realm=toronto",
  "companyInfo": {"companyName": "City of Toronto"},
  "categories": [
    {"categoryCode": 72141105, "categoryName": "Sidewalk construction and repair service"},
    {"categoryCode": 72141121, "categoryName": "Water main construction service"}
  ],
  "opportunityAmount": {"maxAmount": 99000000, "minAmount": 1000, "currency": "CAD"}
}
```

- [ ] **Step 2: Write the failing normalize tests**

Add to `scrapers/tests/test_ariba.py`:

```python
import json
from pathlib import Path

from toronto_bids.models import AribaPosting
from toronto_bids.sources import ariba

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_normalize_with_detail_bridges_and_snapshots():
    raw = {"search": _fixture("ariba_search_record.json"), "detail": _fixture("ariba_detail.json")}
    posts = list(ariba.normalize_posting(raw))
    assert len(posts) == 1
    p = posts[0]
    assert isinstance(p, AribaPosting)
    assert p.rfx_id == "1110015885"
    assert p.document_number == "5672751291"          # bridged from externalRfxId
    assert p.external_rfx_id == "Doc5672751291"
    assert p.status == "PUBLISHED"
    assert p.customer_name == "City of Toronto"
    assert p.close_date == "2026-07-17T09:00:00-07:00"  # search endDate preferred
    assert p.currency == "CAD"
    assert p.amount_max == "99000000"                 # from detail opportunityAmount
    assert p.public_posting_url == "https://discovery.ariba.com/rfx/1110015885"
    assert "s1.ariba.com" in p.sourcing_url
    assert json.loads(p.categories) == ["Sidewalk construction and repair service",
                                        "Water main construction service"]
    assert json.loads(p.raw_json)["externalRfxId"] == "Doc5672751291"  # snapshot present
    assert p.source == "ariba_discovery"


def test_normalize_without_detail_archives_search_only():
    raw = {"search": _fixture("ariba_search_record.json"), "detail": None}
    p = list(ariba.normalize_posting(raw))[0]
    assert p.rfx_id == "1110015885"
    assert p.document_number is None          # no externalRfxId, no Doc in this title
    assert p.raw_json is None                 # nothing to snapshot
    assert p.external_rfx_id is None
    assert p.title == "Request for Tenders for Watermain and Sewer Replacement on various roads"
    assert p.customer_name == "City of Toronto"
    assert p.amount_max == "70542967.0799487"  # falls back to search maxAmount
    assert p.currency is None
    assert json.loads(p.categories) == ["Sidewalk construction and repair service",
                                        "Sewer line construction service"]


def test_normalize_without_detail_bridges_title_embedded_doc():
    search = dict(_fixture("ariba_search_record.json"))
    search["title"] = "Doc5581608073 - Request for Quotations for supplies"
    p = list(ariba.normalize_posting({"search": search, "detail": None}))[0]
    assert p.document_number == "5581608073"   # bridged from the title
```

- [ ] **Step 3: Run the normalize tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_ariba.py -k normalize -v`
Expected: FAIL — `ariba.normalize_posting` not defined / `NotImplementedError`.

- [ ] **Step 4: Implement `normalize_posting`**

In `scrapers/toronto_bids/sources/ariba.py`, add imports at the top:

```python
import json

from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaPosting
```

Add helpers and the normalizer above the class:

```python
def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _categories(search: dict, detail: dict | None) -> str | None:
    if detail and detail.get("categories"):
        names = [c.get("categoryName") for c in detail["categories"] if c.get("categoryName")]
        return json.dumps(names) if names else None
    cats = search.get("productsAndServicesCategories")
    return json.dumps(cats) if cats else None


def normalize_posting(raw: dict):
    search = raw["search"]
    detail = raw.get("detail")
    rfx_id = str(search["rfxID"])
    title = _clean(search.get("title")) or (_clean(detail.get("title")) if detail else None)
    external = _clean(detail.get("externalRfxId")) if detail else None

    if detail and detail.get("opportunityAmount"):
        amt = detail["opportunityAmount"]
        amount_min, amount_max = _clean(amt.get("minAmount")), _clean(amt.get("maxAmount"))
        currency = _clean(amt.get("currency"))
    else:
        amount_min, amount_max = _clean(search.get("minAmount")), _clean(search.get("maxAmount"))
        currency = None

    yield AribaPosting(
        rfx_id=rfx_id,
        document_number=bridge_document_number(external, title),
        title=title,
        posting_type=_clean(detail.get("type")) if detail else _clean(search.get("rfxType")),
        status=_clean(detail.get("status")) if detail else None,
        customer_name=_clean(search.get("customerName")),
        posted_date=_clean(search.get("datePosted")) or (_clean(detail.get("startDate")) if detail else None),
        close_date=_clean(search.get("endDate")) or (_clean(detail.get("endDate")) if detail else None),
        categories=_categories(search, detail),
        amount_min=amount_min,
        amount_max=amount_max,
        currency=currency,
        public_posting_url=_clean(detail.get("publicPostingUrl")) if detail else None,
        sourcing_url=_clean(detail.get("sourcingUrl")) if detail else None,
        external_rfx_id=external,
        raw_json=json.dumps(detail) if detail else None,
        source="ariba_discovery",
    )
```

Replace the `normalize` stub in the class with:

```python
    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_posting(raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_ariba.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba.py scrapers/tests/test_ariba.py scrapers/tests/fixtures/ariba_search_record.json scrapers/tests/fixtures/ariba_detail.json
git commit -m "feat(scraper): add Ariba posting normalizer with document_number bridge"
```

---

### Task 5: Wire into pipeline + integration test + live smoke + docs

**Files:**
- Modify: `scrapers/toronto_bids/pipeline.py`
- Modify: `scrapers/tests/test_pipeline_integration.py`
- Modify: `scrapers/README.md`

**Interfaces:**
- Consumes: `AribaDiscoverySource`, `db.upsert_row`, the `conn` test fixture.
- Produces: `ariba_discovery` in `default_sources()`; `tb sync`/`tb status` include `ariba_posting`.

- [ ] **Step 1: Add the source to the pipeline**

In `scrapers/toronto_bids/pipeline.py`, import and append it:

```python
from toronto_bids.sources.ariba import AribaDiscoverySource
```

Append to the `default_sources()` return list (after the CKAN sources):
```python
        AribaDiscoverySource(),
```

- [ ] **Step 2: Write the failing integration test**

Add to `scrapers/tests/test_pipeline_integration.py`:

```python
def test_ariba_fetch_normalize_upsert_bridges_and_archives(conn):
    from toronto_bids.sources.ariba import normalize_posting
    from toronto_bids.store import db

    search = _load("ariba_search_record.json")
    detail = _load("ariba_detail.json")
    # Simulate the fetch output: one detail-200 posting, one detail-500 posting.
    raws = [
        {"search": search, "detail": detail},
        {"search": {**search, "rfxID": "1110099999", "title": "no doc here"}, "detail": None},
    ]
    for raw in raws:
        for row in normalize_posting(raw):
            db.upsert_row(conn, row, overwrite=True)
    conn.commit()

    assert db.counts(conn)["ariba_posting"] == 2
    bridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert bridged["document_number"] == "5672751291"   # linked to the OData/CKAN spine
    assert bridged["raw_json"] is not None               # snapshot archived
    unbridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110099999'"
    ).fetchone()
    assert unbridged["document_number"] is None          # archived even though un-bridged
    assert unbridged["raw_json"] is None
```

(`_load` and the `conn` fixture already exist in this file / conftest. The fixtures live in `tests/fixtures/`, which `_load` reads.)

- [ ] **Step 3: Run the integration test to verify it fails, then passes**

Run: `cd scrapers && uv run pytest tests/test_pipeline_integration.py -v`
Expected: the new test initially fails only if Step 1/prior tasks are incomplete; with Tasks 1–4 done it PASSES. Then run the full suite:

Run: `cd scrapers && uv run pytest -v`
Expected: every test PASSES, output pristine.

- [ ] **Step 4: Live end-to-end smoke check (network — manual, not a test)**

Run:
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-p2 uv run tb sync --only ariba_discovery && uv run tb status
```
Expected: completes without error; `tb status` shows `ariba_posting` in the low tens (~42). Then inspect the split:
```bash
sqlite3 /tmp/tb-p2/bids.sqlite "SELECT COUNT(*) total, COUNT(document_number) bridged, COUNT(raw_json) with_detail FROM ariba_posting;"
sqlite3 /tmp/tb-p2/bids.sqlite "SELECT status FROM sync_run WHERE source='ariba_discovery';"
```
Expected: `total` ≈ 42; `bridged` and `with_detail` are a majority but < total (the ~40% that 500'd are archived with NULL doc/raw_json); `sync_run` status `ok`. Record the actual numbers in the report. Do **not** block the commit on the exact bridged ratio — it varies per run by design.

- [ ] **Step 5: Update the README**

In `scrapers/README.md`, add `ariba_discovery` to the sources list and note the archive semantics. Under the sources section, add:

```markdown
- **SAP Ariba Discovery** (`ariba_discovery`) — archives currently-open City-of-Toronto
  Ariba postings (`ariba_posting` table) before they close, via public JSON APIs (no auth).
  Each posting is bridged to its `document_number` where the detail endpoint resolves
  (~40% return HTTP 500 on a given run and are archived un-bridged; idempotent re-runs fill
  the gap). The `sourcing_url` column is the authenticated event link for a future
  attachments phase.
```

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/pipeline.py scrapers/tests/test_pipeline_integration.py scrapers/README.md
git commit -m "feat(scraper): wire ariba_discovery into pipeline; integration test + docs"
```

---

## Self-Review

**1. Spec coverage (design §2.1 #6/#7, §3.2, §5, §10 P2):**
- Search API `doIndexedSearch` + City-of-Toronto filter → Task 3 (`fetch`). ✓
- Detail API `/rfx/{id}` with `Accept: application/json`, ~40% 500 handled by retry-then-skip → Task 3 (`fetch` per-posting try/except; `HttpClient` retries). ✓
- rfxID→document_number bridge via `externalRfxId` (strip) + title `Doc\d{10}` fallback; **no** OData-link / fuzzy → Task 2 + Task 4. ✓
- `ariba_posting` table with raw JSON snapshot + archive timing (design §5) → Task 1. ✓
- Archive every posting whether bridged or not; never delete; overwrite=True keeps snapshot across a later 500 → Tasks 1, 4 (+ `test_ariba_posting_later_500_does_not_wipe_snapshot`, `test_normalize_without_detail_archives_search_only`). ✓
- Wire into `default_sources` → Task 5. ✓
- `sourcing_url` retained for the later attachments phase (design §2.2) → Task 1 model + Task 4. ✓
- Out of scope by design (authenticated attachment fetch = P4; TMMIS/PDF = P5; export seam = P3) → not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code. The only `NotImplementedError` (Task 3 `AribaDiscoverySource.normalize`) is explicitly completed in Task 4 and not exercised before then. ✓

**3. Type consistency:** `AribaPosting` field names match across models (Task 1), `_ARIBA_POSTING_COLS` (Task 1), and the normalizer (Task 4). `bridge_document_number(external_rfx_id, title)` signature matches between Task 2 and its Task 4 caller. `fetch` yields `{"search", "detail"}` dicts (Task 3) exactly as `normalize_posting` consumes them (Task 4). `AribaDiscoverySource` attributes (`name="ariba_discovery"`, `overwrite=True`) are consistent across Tasks 3, 5 and the `Source` protocol. `HttpClient.get_json/post_json` gain `headers=` consistently (Task 3) and the sole new caller passes `headers={"Accept": "application/json"}`. `db.counts` gains `"ariba_posting"` (Task 1), which `tb status` renders automatically. ✓
