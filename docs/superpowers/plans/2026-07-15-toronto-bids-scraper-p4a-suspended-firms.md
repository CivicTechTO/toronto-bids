# Toronto Bids Scraper — P4a: Suspended & Disqualified Firms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the City of Toronto "Suspended & Disqualified Firms" registry (a public HTML table) into a new `suspended_firm` table, wired into the pipeline and the JSON export.

**Architecture:** A new source adapter (`SuspendedFirmsSource`) follows the established `fetch() → normalize()` pattern. `fetch()` GETs the public page and parses its single `<table>` into per-row header→cell dicts via a pure `parse_suspended_table` helper; `normalize()` maps each row dict to a `SuspendedFirm`. Idempotent upsert keyed on `(supplier_name_raw, council_authority)`.

**Tech Stack:** Python 3.12+, `uv`, `httpx`, `lxml` (new dep, for HTML parsing), SQLite, `pytest`. No auth, no browser — this reads a public page.

**Scope note:** This is **P4a**. The authenticated Ariba attachment fetcher (**P4b**) is a separate plan, written after a grounded exploration of the authenticated flow. This plan does not touch credentials, Playwright, or the `attachment` table.

## Global Constraints

- **Python 3.12+**, managed with **uv** only (`uv add lxml` for the new dependency — do not pin unless needed). All `uv`/`pytest` commands run from inside `scrapers/`. Package import name `toronto_bids`.
- **Builds on the merged P0/P1 + P2 + P3 package.** Reuse, do not duplicate: `store/db.py` (`connect`, `init_db`, `counts`, `upsert_row(conn, row, *, overwrite)`, `_upsert_keyed`), `store/schema.sql`, `http.HttpClient` (`_request(method, url, headers=None, **kwargs)`, `get_json`, `post_json`), `sources/base.Source` protocol (`name`, `overwrite`, `fetch`, `normalize`) and its `Row` union, `pipeline.default_sources`, `export/document.build_export_document`, `config.py`, `models.py`.
- **`SuspendedFirmsSource.overwrite = True`** (the registry page is authoritative). `name = "suspended_firms"`.
- **Never delete; `first_seen`/`last_seen`** (TEXT, `datetime('now')`) on the new table; upserts touch `last_seen` (handled by the existing `_upsert_keyed`).
- **Idempotency key:** `UNIQUE(supplier_name_raw, council_authority)` — a firm suspended under a given council decision (Authority, e.g. `2025.GG19.17`, `GM18.4`) is one row.
- **Source URL** (verified live 2026-07-15): `https://www.toronto.ca/business-economy/doing-business-with-the-city/searching-bidding-on-city-contracts/suspended-disqualified-firms/`. One `<table>` with a `<thead>` of exactly: `Supplier Name`, `Status`, `Start Date of Suspension`, `End Date of Suspension`, `Type of Suspension`, `Authority`. Currently 3 `<tbody>` rows. Statuses seen: `Suspended`, `Permanent Suspension`, `Disqualified`. Dates are free text (e.g. `March 27, 2025`, `November 27, 28, and 29, 2012`, `N/A`) — **store them verbatim as strings; do not parse to dates.**
- **Parsing is header-driven, not positional:** map each row's cells to the `<thead>` column names, so a future column reorder doesn't silently misalign fields.
- **No network in unit tests** — parse from a saved HTML fixture; `fetch` is exercised via `MockTransport`.

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` (§2.2 source #9, §5 `suspended_firm`, §10 P4).

**Base branch:** `p4-tier2` (off `p3-export-seam`).

---

## File Structure

New and modified files (all under `scrapers/`):

```
scrapers/
  pyproject.toml               # MODIFY: add lxml dependency (via `uv add lxml`)
  toronto_bids/
    models.py                  # MODIFY: add SuspendedFirm dataclass
    config.py                  # MODIFY: add SUSPENDED_FIRMS_URL
    http.py                    # MODIFY: add get_text()
    pipeline.py                # MODIFY: add SuspendedFirmsSource to default_sources
    store/
      schema.sql               # MODIFY: add suspended_firm table
      db.py                    # MODIFY: _SUSPENDED_COLS, upsert_row branch, counts()
    sources/
      suspended_firms.py       # CREATE: parse_suspended_table + SuspendedFirmsSource
    export/
      document.py              # MODIFY: add suspended_firms top-level array
  tests/
    fixtures/
      suspended_firms.html     # CREATE: real trimmed page table
    test_db.py                 # MODIFY: suspended_firm upsert/counts tests
    test_http.py               # MODIFY: get_text test
    test_suspended_firms.py    # CREATE: parse + fetch + normalize tests
    test_export_document.py    # MODIFY: suspended_firms in the export
```

---

### Task 1: `SuspendedFirm` model + `suspended_firm` table + store wiring

**Files:**
- Modify: `scrapers/toronto_bids/models.py`
- Modify: `scrapers/toronto_bids/store/schema.sql`
- Modify: `scrapers/toronto_bids/store/db.py`
- Modify: `scrapers/tests/test_db.py`

**Interfaces:**
- Consumes: `db.upsert_row`, `db.counts`.
- Produces: `models.SuspendedFirm` (frozen dataclass; `supplier_name_raw` first field, all fields default `None` except `source: str = ""`); `suspended_firm` table keyed by `UNIQUE(supplier_name_raw, council_authority)`; `db.upsert_row` routes `SuspendedFirm`; `db.counts` includes `"suspended_firm"`.

- [ ] **Step 1: Add the `SuspendedFirm` dataclass**

Append to `scrapers/toronto_bids/models.py`:

```python
@dataclass(frozen=True)
class SuspendedFirm:
    supplier_name_raw: str
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    suspension_type: str | None = None
    council_authority: str | None = None
    source: str = ""
```

- [ ] **Step 2: Add the `suspended_firm` table**

Append to `scrapers/toronto_bids/store/schema.sql`:

```sql

-- suspended_firm mirrors the City's Suspended & Disqualified Firms registry (public HTML table).
-- Keyed on (supplier_name_raw, council_authority): one row per firm per council decision.
CREATE TABLE IF NOT EXISTS suspended_firm (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name_raw  TEXT NOT NULL,
    status             TEXT,
    start_date         TEXT,
    end_date           TEXT,
    suspension_type    TEXT,
    council_authority  TEXT,
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (supplier_name_raw, council_authority)
);
```

- [ ] **Step 3: Write the failing store tests**

Add to `scrapers/tests/test_db.py` (extend the existing `from toronto_bids.models import ...` line to include `SuspendedFirm`):

```python
def test_upsert_suspended_firm_is_idempotent(conn):
    from toronto_bids.models import SuspendedFirm
    firm = SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                         start_date="March 27, 2025", council_authority="2025.GG19.17",
                         source="suspended_firms")
    db.upsert_row(conn, firm, overwrite=True)
    db.upsert_row(conn, firm, overwrite=True)
    assert db.counts(conn)["suspended_firm"] == 1


def test_upsert_suspended_firm_distinct_authority_is_new_row(conn):
    from toronto_bids.models import SuspendedFirm
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Acme", council_authority="A1",
                                      source="suspended_firms"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Acme", council_authority="A2",
                                      source="suspended_firms"), overwrite=True)
    assert db.counts(conn)["suspended_firm"] == 2


def test_suspended_firm_overwrite_updates_status(conn):
    from toronto_bids.models import SuspendedFirm
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Reinstated",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    row = conn.execute("SELECT status FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'").fetchone()
    assert row["status"] == "Reinstated"


def test_counts_includes_suspended_firm(conn):
    assert "suspended_firm" in db.counts(conn)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_db.py -k suspended -v`
Expected: FAIL — `SuspendedFirm` not importable / `counts` has no `suspended_firm` key.

- [ ] **Step 5: Wire the store**

In `scrapers/toronto_bids/store/db.py`:

Extend the models import:
```python
from toronto_bids.models import Award, NonCompetitive, Solicitation, AribaPosting, SuspendedFirm
```

Add the column list after the existing `_ARIBA_POSTING_COLS`:
```python
_SUSPENDED_COLS = [
    "supplier_name_raw", "status", "start_date", "end_date",
    "suspension_type", "council_authority", "source",
]
```

Add a branch in `upsert_row` (before the final `else`):
```python
    elif isinstance(row, SuspendedFirm):
        values = [getattr(row, c) for c in _SUSPENDED_COLS]
        _upsert_keyed(conn, "suspended_firm", _SUSPENDED_COLS, values,
                      ["supplier_name_raw", "council_authority"], overwrite)
```

Add `"suspended_firm"` to the `counts` table list (before `"sync_run"`):
```python
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting", "suspended_firm", "sync_run"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/models.py scrapers/toronto_bids/store scrapers/tests/test_db.py
git commit -m "feat(scraper): add suspended_firm table, model, and store wiring"
```

---

### Task 2: `parse_suspended_table` + `get_text` + `SuspendedFirmsSource`

**Files:**
- Modify: `scrapers/pyproject.toml` (via `uv add lxml`)
- Modify: `scrapers/toronto_bids/config.py`
- Modify: `scrapers/toronto_bids/http.py`
- Create: `scrapers/toronto_bids/sources/suspended_firms.py`
- Create: `scrapers/tests/fixtures/suspended_firms.html`
- Modify: `scrapers/tests/test_http.py`
- Create: `scrapers/tests/test_suspended_firms.py`

**Interfaces:**
- Consumes: `HttpClient.get_text`, `normalize_document_number` is NOT used here, `models.SuspendedFirm`, `config.SUSPENDED_FIRMS_URL`.
- Produces:
  - `HttpClient.get_text(url, params=None, headers=None) -> str`
  - `suspended_firms.parse_suspended_table(html_str: str) -> list[dict]` (each dict maps the `<thead>` column text → that row's cell text; only the first `<table>` is read).
  - `suspended_firms.SuspendedFirmsSource` (`name="suspended_firms"`, `overwrite=True`; `fetch(http)` yields raw row dicts; `normalize(raw)` yields one `SuspendedFirm`).

- [ ] **Step 1: Add the lxml dependency**

Run:
```bash
cd scrapers && uv add lxml
```
Expected: `pyproject.toml` gains `lxml` under `[project.dependencies]`; `uv.lock` updates. Commit these together in Step 9.

- [ ] **Step 2: Add config + get_text**

Append to `scrapers/toronto_bids/config.py`:
```python
# Suspended & Disqualified Firms registry (public HTML, no auth).
SUSPENDED_FIRMS_URL = (
    "https://www.toronto.ca/business-economy/doing-business-with-the-city/"
    "searching-bidding-on-city-contracts/suspended-disqualified-firms/"
)
```

Add to `scrapers/toronto_bids/http.py` (after `post_json`):
```python
    def get_text(self, url, params=None, headers=None) -> str:
        return self._request("GET", url, params=params, headers=headers).text
```

- [ ] **Step 3: Create the HTML fixture**

`scrapers/tests/fixtures/suspended_firms.html` (real page structure, trimmed to the table + wrapping markup so the parser must find the right table):

```html
<html><body>
<h1>Suspended and Disqualified Firms</h1>
<table>
  <thead><tr>
    <th>Supplier Name</th><th>Status</th><th>Start Date of Suspension</th>
    <th>End Date of Suspension</th><th>Type of Suspension</th><th>Authority</th>
  </tr></thead>
  <tbody>
    <tr><td>Capital Sewers Services Inc. and Affiliated Persons</td><td>Suspended</td><td>December 16, 2025</td><td>December 16, 2030</td><td>Supplier Code of Conduct</td><td>2025.GG26.3</td></tr>
    <tr><td>Duron Ontario Ltd.</td><td>Suspended</td><td>March 27, 2025</td><td>March 27, 2030</td><td>Supplier Code of Conduct</td><td>2025.GG19.17</td></tr>
    <tr><td>Entities Owned, Directed or Controlled by X</td><td>Permanent Suspension</td><td>November 27, 28, and 29, 2012</td><td>N/A</td><td>N/A</td><td>GM18.4</td></tr>
  </tbody>
</table>
<p>Date modified: December 18, 2025</p>
</body></html>
```

- [ ] **Step 4: Write the failing parse/normalize/fetch tests**

`scrapers/tests/test_suspended_firms.py`:

```python
from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources import suspended_firms
from toronto_bids.sources.suspended_firms import SuspendedFirmsSource, parse_suspended_table

FIXTURES = Path(__file__).parent / "fixtures"


def _html():
    return (FIXTURES / "suspended_firms.html").read_text()


def test_parse_returns_one_dict_per_row_keyed_by_header():
    rows = parse_suspended_table(_html())
    assert len(rows) == 3
    assert rows[1] == {
        "Supplier Name": "Duron Ontario Ltd.",
        "Status": "Suspended",
        "Start Date of Suspension": "March 27, 2025",
        "End Date of Suspension": "March 27, 2030",
        "Type of Suspension": "Supplier Code of Conduct",
        "Authority": "2025.GG19.17",
    }


def test_parse_handles_na_and_multiday_dates_verbatim():
    rows = parse_suspended_table(_html())
    assert rows[2]["Status"] == "Permanent Suspension"
    assert rows[2]["Start Date of Suspension"] == "November 27, 28, and 29, 2012"
    assert rows[2]["End Date of Suspension"] == "N/A"
    assert rows[2]["Authority"] == "GM18.4"


def test_normalize_maps_row_dict_to_suspended_firm():
    rows = parse_suspended_table(_html())
    firm = list(SuspendedFirmsSource().normalize(rows[1]))[0]
    assert isinstance(firm, SuspendedFirm)
    assert firm.supplier_name_raw == "Duron Ontario Ltd."
    assert firm.status == "Suspended"
    assert firm.start_date == "March 27, 2025"
    assert firm.end_date == "March 27, 2030"
    assert firm.suspension_type == "Supplier Code of Conduct"
    assert firm.council_authority == "2025.GG19.17"
    assert firm.source == "suspended_firms"


def test_fetch_gets_page_and_yields_row_dicts():
    def handler(request):
        return httpx.Response(200, text=_html())
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)
    rows = list(SuspendedFirmsSource().fetch(http))
    assert len(rows) == 3
    assert rows[0]["Supplier Name"].startswith("Capital Sewers")


def test_source_attributes():
    src = SuspendedFirmsSource()
    assert src.name == "suspended_firms"
    assert src.overwrite is True
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_suspended_firms.py -v`
Expected: FAIL — `toronto_bids.sources.suspended_firms` not importable.

- [ ] **Step 6: Implement the parser + source**

`scrapers/toronto_bids/sources/suspended_firms.py`:

```python
from typing import Iterable

from lxml import html

from toronto_bids import config
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources.base import Row


def parse_suspended_table(html_str: str) -> list[dict]:
    """Parse the first <table> into a list of header->cell dicts (one per body row)."""
    root = html.fromstring(html_str)
    tables = root.xpath("//table")
    if not tables:
        return []
    table = tables[0]
    headers = [th.text_content().strip() for th in table.xpath(".//thead//th")]
    rows = []
    for tr in table.xpath(".//tbody//tr"):
        cells = [td.text_content().strip() for td in tr.xpath("./td")]
        if not cells:
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


class SuspendedFirmsSource:
    name = "suspended_firms"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        page = http.get_text(config.SUSPENDED_FIRMS_URL)
        yield from parse_suspended_table(page)

    def normalize(self, raw: dict) -> Iterable[Row]:
        name = (raw.get("Supplier Name") or "").strip()
        if not name:
            return
        yield SuspendedFirm(
            supplier_name_raw=name,
            status=(raw.get("Status") or "").strip() or None,
            start_date=(raw.get("Start Date of Suspension") or "").strip() or None,
            end_date=(raw.get("End Date of Suspension") or "").strip() or None,
            suspension_type=(raw.get("Type of Suspension") or "").strip() or None,
            council_authority=(raw.get("Authority") or "").strip() or None,
            source="suspended_firms",
        )
```

Note: `SuspendedFirm` is a new member of the store's row types but is **not** in `base.Row`. Add it to the `Row` union in `scrapers/toronto_bids/sources/base.py`:
```python
from toronto_bids.models import AribaPosting, Award, NonCompetitive, Solicitation, SuspendedFirm

Row = Solicitation | Award | NonCompetitive | AribaPosting | SuspendedFirm
```

- [ ] **Step 7: Write the failing get_text test**

Add to `scrapers/tests/test_http.py`:
```python
def test_get_text_returns_body_text():
    def handler(request):
        return httpx.Response(200, text="<html>hi</html>")
    client = _client(handler)
    assert client.get_text("https://example.test/x") == "<html>hi</html>"
```
(The `_client` helper already exists at the top of `test_http.py`.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_suspended_firms.py tests/test_http.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add scrapers/pyproject.toml scrapers/uv.lock scrapers/toronto_bids/config.py scrapers/toronto_bids/http.py scrapers/toronto_bids/sources/suspended_firms.py scrapers/toronto_bids/sources/base.py scrapers/tests/fixtures/suspended_firms.html scrapers/tests/test_suspended_firms.py scrapers/tests/test_http.py
git commit -m "feat(scraper): add suspended-firms parser and source (lxml)"
```

---

### Task 3: Wire into pipeline + export + live smoke + docs

**Files:**
- Modify: `scrapers/toronto_bids/pipeline.py`
- Modify: `scrapers/toronto_bids/export/document.py`
- Modify: `scrapers/tests/test_export_document.py`
- Modify: `scrapers/README.md`

**Interfaces:**
- Consumes: `SuspendedFirmsSource`, `build_export_document`, the `conn` test fixture.
- Produces: `suspended_firms` in `default_sources()`; a `suspended_firms` top-level array in the export document.

- [ ] **Step 1: Add the source to the pipeline**

In `scrapers/toronto_bids/pipeline.py`, import and append it:
```python
from toronto_bids.sources.suspended_firms import SuspendedFirmsSource
```
Append to the `default_sources()` return list (after `AribaDiscoverySource()`):
```python
        SuspendedFirmsSource(),
```

- [ ] **Step 2: Write the failing export test**

Add to `scrapers/tests/test_export_document.py`:
```python
def test_suspended_firms_is_separate_top_level(conn):
    from toronto_bids.models import SuspendedFirm
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suspended_firms"]) == 1
    firm = doc["suspended_firms"][0]
    assert firm["supplier_name_raw"] == "Duron Ontario Ltd."
    assert firm["council_authority"] == "2025.GG19.17"
    assert "id" not in firm


def test_suspended_firms_empty_when_none(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["suspended_firms"] == []
```

- [ ] **Step 3: Run the export test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_export_document.py -k suspended -v`
Expected: FAIL — `doc["suspended_firms"]` KeyError.

- [ ] **Step 4: Add suspended_firms to the export document**

In `scrapers/toronto_bids/export/document.py`, inside `build_export_document`, add after the `noncompetitive` block (drop the surrogate `id`):
```python
    suspended_firms = [
        _drop(firm, "id")
        for firm in _rows(conn, "SELECT * FROM suspended_firm ORDER BY supplier_name_raw, council_authority")
    ]
```
Add `"suspended_firms": suspended_firms,` to the returned dict (alongside `noncompetitive`).

- [ ] **Step 5: Run the full suite**

Run: `cd scrapers && uv run pytest -v`
Expected: every test PASSES, output pristine.

- [ ] **Step 6: Live smoke check (network — manual, not a test)**

Run:
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-p4a uv run tb sync --only suspended_firms && uv run tb status
```
Expected: completes; `tb status` shows `suspended_firm` in the low single digits (currently 3). Then:
```bash
sqlite3 /tmp/tb-p4a/bids.sqlite "SELECT supplier_name_raw, status, council_authority FROM suspended_firm;"
```
Expected: the real current rows (e.g. Duron Ontario Ltd. / Suspended / 2025.GG19.17). Record the actual rows in the report. Do not block the commit on the exact count.

- [ ] **Step 7: Update the README**

In `scrapers/README.md`, add `suspended_firms` to the sources list:
```markdown
- **Suspended & Disqualified Firms** (`suspended_firms`) — the City's public registry of
  suspended/disqualified suppliers (`suspended_firm` table), parsed from the HTML table. Each
  row carries the supplier name, status, suspension dates, type, and the council `Authority`
  reference. Exported as a top-level `suspended_firms` array.
```

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/pipeline.py scrapers/toronto_bids/export/document.py scrapers/tests/test_export_document.py scrapers/README.md
git commit -m "feat(scraper): wire suspended_firms into pipeline and export"
```

---

## Self-Review

**1. Spec coverage (design §2.2 #9, §5, §10 P4a):**
- Suspended-firms public HTML table, header-driven parse → Task 2 (`parse_suspended_table`). ✓
- `suspended_firm` table (design §5) with council `Authority` → Task 1. ✓
- Dates stored verbatim (free-text `N/A`, multi-day) → Task 1 model (TEXT) + Task 2 parse tests. ✓
- Idempotent on `(supplier_name_raw, council_authority)`; never delete; first_seen/last_seen → Task 1. ✓
- `get_text` on the HTTP client (HTML, not JSON) → Task 2. ✓
- Wired into `default_sources` + `tb status` counts → Tasks 1, 3. ✓
- Included in the JSON export as a top-level array → Task 3. ✓
- `SuspendedFirm` added to `base.Row` union → Task 2. ✓
- Out of scope by design (attachment fetcher = P4b; supplier-dim/council-bridge linkage = P5) → not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; every command shows expected output. No `NotImplementedError` stubs. ✓

**3. Type consistency:** `SuspendedFirm` field names match across the dataclass (Task 1), `_SUSPENDED_COLS` (Task 1), the normalizer (Task 2), and the export drop (Task 3). `parse_suspended_table(html_str) -> list[dict]` and `SuspendedFirmsSource` (`name="suspended_firms"`, `overwrite=True`, `fetch`/`normalize`) are consistent across Tasks 2 and 3. `get_text(url, params=None, headers=None)` matches the sole caller in `fetch`. `db.counts` gains `"suspended_firm"` (Task 1), rendered by `tb status` automatically and asserted in Task 1. The export adds `suspended_firms` used identically in the builder (Task 3) and its tests. `SuspendedFirm` is added to the `Row` union (Task 2). ✓
