# bids&tenders Capture (#135) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture Toronto Zoo and TRCA procurement into a new buyer-keyed agency layer — board-report awards now, portal listings later behind a written-permission gate — per `docs/superpowers/specs/2026-07-18-bidsandtenders-adapter-design.md`.

**Architecture:** A `buyer` dimension plus `agency_solicitation`/`agency_award`/`agency_bid` tables (fourth keyspace: `(buyer_id, native_ref)`; City spine untouched). A new `tb enrich-agencies` command walks TRCA's eSCRIBE/Laserfiche meeting records (plain HTTP) and the Zoo's ZB committee on TMMIS (reusing `bid_award_panel`'s prober), stores report PDFs in `background_pdf`, and feeds pure text parsers. The portal source exists only as a config-gated stub until permission lands.

**Tech Stack:** Python 3.12, `uv`, sqlite3, pytest (offline, fixture-based), `pdftotext` for PDFs, existing `HttpClient`; Playwright (council extra) only for Zoo TMMIS agenda discovery.

## Global Constraints

- All tests offline, fixture-based, no network (house rule). Run from `scrapers/`: `uv run pytest`.
- No lint/format/typecheck exists — do not invent those commands.
- **Never fetch any `*.bidsandtenders.ca` page.** The portal source must raise unless its per-body `enabled` flag is true, and both flags stay false in this plan.
- Amounts: raw TEXT verbatim + `*_numeric` via `toronto_bids/amount.py` only. Never aggregate raw TEXT. Store the extracted dollar token as the raw value, never the surrounding phrase (#96).
- `db._upsert_keyed`'s conflict target must match any expression index exactly (`_CONFLICT_TARGETS`).
- Rows are never deleted; `first_seen`/`last_seen` on every data table; download queues key on `sha256 IS NULL`, never on text (#96).
- Parsers refuse rather than guess: the TRCA results table is fused multi-line pdftotext output (the #83 wrapped-names trap) — per-bid prices are NOT extracted from it; bidders come from the clean bullet list, winners+amounts from the RECOMMENDATION block.
- Commit messages end with the project's Co-Authored-By trailer. Work on branch `feat-135-bidsandtenders`.

---

### Task 1: buyer dimension + agency tables (schema, models, upsert plumbing)

**Files:**
- Modify: `scrapers/toronto_bids/store/schema.sql` (append after the `ariba_attachment` block)
- Modify: `scrapers/toronto_bids/models.py` (append)
- Modify: `scrapers/toronto_bids/store/db.py:10-37` (`_TABLES`, `_CONFLICT_TARGETS`) and `counts()`
- Create: `scrapers/toronto_bids/buyers.py`
- Test: `scrapers/tests/test_agencies.py`

**Interfaces:**
- Produces: models `Buyer(slug, name, kind, partnered, funding_share, platform, notes)`, `AgencySolicitation(buyer_id, native_ref, title, status, posted_date, closing_date, portal_url, source)`, `AgencyAward(buyer_id, native_ref, supplier_name_raw, award_amount, value_confidential, award_date, report_url, source)` (+ derived `award_amount_numeric`), `AgencyBid(buyer_id, native_ref, bidder_name_raw, bid_price, report_url, source)` (+ derived `bid_price_numeric`); `buyers.seed_buyers(conn) -> dict[str, int]` mapping slug→buyer_id. All later tasks consume these exact names.

- [ ] **Step 1: Write the failing tests**

```python
# scrapers/tests/test_agencies.py
import sqlite3

import pytest

from toronto_bids.buyers import DEFAULT_BUYERS, seed_buyers
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation
from toronto_bids.store import db


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    yield conn
    conn.close()


def test_seed_buyers_is_idempotent(conn):
    ids = seed_buyers(conn)
    assert set(ids) == {"toronto-zoo", "trca"}
    again = seed_buyers(conn)
    assert ids == again
    assert conn.execute("SELECT COUNT(*) FROM buyer").fetchone()[0] == len(DEFAULT_BUYERS)


def test_trca_is_partnered_with_funding_share(conn):
    seed_buyers(conn)
    row = conn.execute("SELECT * FROM buyer WHERE slug='trca'").fetchone()
    assert row["partnered"] == 1
    assert row["funding_share"] == 0.626
    assert row["kind"] == "agency"


def test_agency_award_upsert_is_idempotent_with_null_amount(conn):
    ids = seed_buyers(conn)
    row = AgencyAward(buyer_id=ids["trca"], native_ref="10039751",
                      supplier_name_raw=None, award_amount=None,
                      value_confidential=1, award_date=None,
                      report_url="https://example.test/r.pdf", source="trca_board")
    db.upsert_row(conn, row, overwrite=True)
    db.upsert_row(conn, row, overwrite=True)   # NULLs must not duplicate (COALESCE key)
    assert conn.execute("SELECT COUNT(*) FROM agency_award").fetchone()[0] == 1


def test_agency_award_numeric_derived(conn):
    ids = seed_buyers(conn)
    row = AgencyAward(buyer_id=ids["trca"], native_ref="10039751",
                      supplier_name_raw='1035477 Ontario Ltd. ("Glenn Windrem Trucking")',
                      award_amount="$1,193,040", value_confidential=0,
                      award_date=None, report_url=None, source="trca_board")
    assert row.award_amount_numeric == 1193040.0
    db.upsert_row(conn, row, overwrite=True)
    got = conn.execute("SELECT award_amount_numeric FROM agency_award").fetchone()[0]
    assert got == 1193040.0


def test_agency_solicitation_backfill_never_overwrites(conn):
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="10039751", title="Portal title",
        status=None, posted_date=None, closing_date=None, portal_url=None,
        source="bids_tenders"), overwrite=True)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="10039751", title="Board title",
        status="awarded", posted_date=None, closing_date=None, portal_url=None,
        source="trca_board"), overwrite=False)
    row = conn.execute("SELECT title, status FROM agency_solicitation").fetchone()
    assert row["title"] == "Portal title"   # backfill only fills NULLs
    assert row["status"] == "awarded"       # ...but does fill them


def test_counts_include_agency_tables(conn):
    got = db.counts(conn)
    for table in ("buyer", "agency_solicitation", "agency_award", "agency_bid"):
        assert table in got
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v`
Expected: FAIL — `ImportError: cannot import name 'seed_buyers'` (module doesn't exist).

- [ ] **Step 3: Append DDL to `schema.sql`**

```sql
-- The buyer dimension and agency tables (#135, first consumer of #103's keyspace decision).
-- Agencies/corporations procure OUTSIDE the City's PMMD feed, each in its own numbering —
-- a FOURTH keyspace, keyed (buyer_id, native_ref). Deliberately separate from the City
-- spine for the same reason composite_award is (#96): admitting foreign-keyed rows to
-- `solicitation` would silently change what every existing COUNT/SUM means. Partnered
-- bodies (TRCA: Toronto pays 62.6% of the levy) carry a flag so exports can segment.
CREATE TABLE IF NOT EXISTS buyer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT NOT NULL UNIQUE,
    name           TEXT,
    kind           TEXT,             -- 'agency' | 'corporation'
    partnered      INTEGER,          -- 1 = not wholly City-owned; segment, don't mix
    funding_share  REAL,             -- Toronto's share where partnered (TRCA: 0.626)
    platform       TEXT,             -- where it posts (bids&tenders here; MERX/Bonfire later)
    notes          TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agency_solicitation (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id     INTEGER NOT NULL,
    -- The body's own identifier, normalized only by trim/uppercase/whitespace-collapse:
    -- TRCA '10039751', Zoo 'RFT-42' / 'RFP 18 (2018-03)'. Where a report names no ref at
    -- all, the TMMIS item reference (e.g. '2025.ZB15.3') stands in. No join to the City
    -- keyspaces is attempted — none can be manufactured.
    native_ref   TEXT NOT NULL,
    title        TEXT,
    status       TEXT,
    posted_date  TEXT,
    closing_date TEXT,
    portal_url   TEXT,
    source       TEXT,
    first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, native_ref)
);

CREATE TABLE IF NOT EXISTS agency_award (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id             INTEGER NOT NULL,
    native_ref           TEXT NOT NULL,
    supplier_name_raw    TEXT,
    supplier_id          INTEGER,
    -- The extracted dollar token verbatim ('$1,193,040'), never the sentence around it
    -- (#96: a phrase leaves *_numeric NULL on every row and zeroes every SUM).
    award_amount         TEXT,
    award_amount_numeric REAL,
    -- 1 = the report routes financials to a CONFIDENTIAL ATTACHMENT (Zoo, 2025-era).
    -- Distinct from "not published": the award happened, the value is deliberately withheld.
    value_confidential   INTEGER DEFAULT 0,
    award_date           TEXT,
    report_url           TEXT,
    source               TEXT,
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE for the same reason as award_line_key (#73): SQLite treats NULLs as DISTINCT in
-- a UNIQUE index, and a confidential award has NULL supplier and NULL amount — a bare key
-- would re-insert it on every run. db._upsert_keyed's conflict target must match exactly.
CREATE UNIQUE INDEX IF NOT EXISTS agency_award_line_key ON agency_award (
    buyer_id, native_ref, COALESCE(supplier_name_raw, ''), COALESCE(award_amount, ''), source
);

CREATE TABLE IF NOT EXISTS agency_bid (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id          INTEGER NOT NULL,
    native_ref        TEXT NOT NULL,
    bidder_name_raw   TEXT NOT NULL,
    supplier_id       INTEGER,
    -- Usually NULL: TRCA results tables are fused multi-line pdftotext output (the #83
    -- wrapped-names trap), so per-bid prices are refused rather than guessed. The bidder
    -- LIST is the competitive fact (#84); prices come from the award lines.
    bid_price         TEXT,
    bid_price_numeric REAL,
    report_url        TEXT,
    source            TEXT,
    first_seen        TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, native_ref, bidder_name_raw, source)
);

CREATE INDEX IF NOT EXISTS idx_agency_award_buyer ON agency_award (buyer_id, native_ref);
CREATE INDEX IF NOT EXISTS idx_agency_bid_buyer ON agency_bid (buyer_id, native_ref);
```

- [ ] **Step 4: Append models to `models.py`**

```python
@dataclass(frozen=True)
class Buyer:
    """One procuring body outside the City's PMMD feed (#135, #103)."""
    slug: str
    name: str | None = None
    kind: str | None = None            # 'agency' | 'corporation'
    partnered: int = 0
    funding_share: float | None = None
    platform: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class AgencySolicitation:
    buyer_id: int
    native_ref: str
    title: str | None = None
    status: str | None = None
    posted_date: str | None = None
    closing_date: str | None = None
    portal_url: str | None = None
    source: str = ""


@dataclass(frozen=True)
class AgencyAward:
    buyer_id: int
    native_ref: str
    supplier_name_raw: str | None = None
    award_amount: str | None = None      # extracted dollar token, verbatim — never summable
    value_confidential: int = 0
    award_date: str | None = None
    report_url: str | None = None
    source: str = ""
    award_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_amount_numeric", parse_amount(self.award_amount))


@dataclass(frozen=True)
class AgencyBid:
    buyer_id: int
    native_ref: str
    bidder_name_raw: str
    bid_price: str | None = None
    report_url: str | None = None
    source: str = ""
    bid_price_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "bid_price_numeric", parse_bid_price(self.bid_price))
```

(`parse_amount` / `parse_bid_price` / `field` are already imported at the top of `models.py` for `Award`/`Bid` — verify, don't re-import.)

- [ ] **Step 5: Register in `db.py`**

Add imports and `_TABLES` entries:

```python
from toronto_bids.models import (Award, Bid, Buyer, AgencyAward, AgencyBid,
                                 AgencySolicitation, CapitalProject, CompositeAward,
                                 NonCompetitive, Solicitation, AribaPosting,
                                 AribaAttachment, SuspendedFirm, Supplier, CouncilItem,
                                 BackgroundPdf)
```

In `_TABLES`:

```python
    Buyer: ("buyer", ["slug"]),
    AgencySolicitation: ("agency_solicitation", ["buyer_id", "native_ref"]),
    AgencyAward: ("agency_award", ["buyer_id", "native_ref", "supplier_name_raw",
                                   "award_amount", "source"]),
    AgencyBid: ("agency_bid", ["buyer_id", "native_ref", "bidder_name_raw", "source"]),
```

In `_CONFLICT_TARGETS`:

```python
    "agency_award": "buyer_id, native_ref, COALESCE(supplier_name_raw, ''), "
                    "COALESCE(award_amount, ''), source",
```

In `counts()`, extend the list with `"buyer", "agency_solicitation", "agency_award", "agency_bid"`.

- [ ] **Step 6: Create `buyers.py`**

```python
"""The buyer dimension seed (#135). Hardcoded like pipeline.default_sources()."""
from toronto_bids.models import Buyer
from toronto_bids.store import db

DEFAULT_BUYERS = [
    Buyer(slug="toronto-zoo", name="Toronto Zoo", kind="agency", partnered=0,
          funding_share=None, platform="bids&tenders",
          notes="Board of Management on TMMIS as the ZB committee; portal "
                "torontozoo.bidsandtenders.ca (gated, #135)."),
    Buyer(slug="trca", name="Toronto and Region Conservation Authority", kind="agency",
          partnered=1, funding_share=0.626, platform="bids&tenders",
          notes="Partnered: six municipalities fund it; Toronto pays 62.6% of the 2025 "
                "operating levy. Bill 97 amalgamates TRCA away 2027-02-01. Venue history "
                "is mixed (Biddingo through ~2023, then trca.bidsandtenders.ca)."),
]


def seed_buyers(conn) -> dict[str, int]:
    """Upsert the hardcoded buyers; return {slug: buyer_id}. Idempotent."""
    for buyer in DEFAULT_BUYERS:
        db.upsert_row(conn, buyer, overwrite=True)
    conn.commit()
    return {r["slug"]: r["id"] for r in conn.execute("SELECT slug, id FROM buyer")}
```

- [ ] **Step 7: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v`
Expected: all 6 PASS. Also run `uv run pytest` — the full suite must stay green (`counts()` change touches `tb status`/nightly paths).

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/store/schema.sql scrapers/toronto_bids/models.py \
        scrapers/toronto_bids/store/db.py scrapers/toronto_bids/buyers.py \
        scrapers/tests/test_agencies.py
git commit -m "feat(agencies): buyer dimension + agency tables — the fourth keyspace (#135)"
```

---

### Task 2: supplier linking spans the agency tables

**Files:**
- Modify: `scrapers/toronto_bids/linking/supplier.py:32-43` (`_SUPPLIER_TABLES`, `_NAME_COLUMN`)
- Test: `scrapers/tests/test_agencies.py` (append)

**Interfaces:**
- Consumes: Task 1's tables/models.
- Produces: `build_supplier_dimension` covering `agency_award` + `agency_bid` (unchanged signature).

- [ ] **Step 1: Write the failing test**

```python
def test_supplier_dimension_spans_agency_tables(conn):
    from toronto_bids.linking.supplier import build_supplier_dimension
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["trca"], native_ref="10039751",
        supplier_name_raw="Gott Natural Stone '99 Inc.", award_amount="$567,648",
        value_confidential=0, award_date=None, report_url=None, source="trca_board"),
        overwrite=True)
    db.upsert_row(conn, AgencyBid(
        buyer_id=ids["trca"], native_ref="10039751",
        bidder_name_raw="H.R. Doornekamp Construction Ltd.", bid_price=None,
        report_url=None, source="trca_board"), overwrite=True)
    n = build_supplier_dimension(conn)
    assert n == 2   # winner + losing bidder both in the dimension
    linked = conn.execute(
        "SELECT COUNT(*) FROM agency_bid WHERE supplier_id IS NOT NULL").fetchone()[0]
    assert linked == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_agencies.py::test_supplier_dimension_spans_agency_tables -v`
Expected: FAIL — `n == 0` (agency tables not scanned).

- [ ] **Step 3: Implement**

In `supplier.py`, extend `_SUPPLIER_TABLES` and `_NAME_COLUMN`:

```python
_SUPPLIER_TABLES = [
    ("award", "id"),
    ("noncompetitive", "workspace_number"),
    ("suspended_firm", "id"),
    ("bid", "id"),
    ("composite_award", "id"),
    # Agency buyers (#135): winners AND losing bidders, so cross-buyer supplier behaviour
    # (a suspended firm bidding at the Zoo; a firm that loses downtown and wins at TRCA)
    # is queryable at all. Same rationale as `bid` (#87).
    ("agency_award", "id"),
    ("agency_bid", "id"),
]

_NAME_COLUMN = {"bid": "bidder_name_raw", "agency_bid": "bidder_name_raw"}
```

- [ ] **Step 4: Run tests**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v` then `uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/linking/supplier.py scrapers/tests/test_agencies.py
git commit -m "feat(agencies): supplier dimension spans agency_award/agency_bid (#135)"
```

---

### Task 3: TRCA board-report parser (pure)

**Files:**
- Create: `scrapers/toronto_bids/sources/trca_board.py` (pure half only)
- Test: `scrapers/tests/test_trca_reports.py`
- Fixtures (already in tree, committed here): `scrapers/tests/fixtures/agencies/trca_armour_stone_2023.txt`, `trca_vor_appraisal_2021.txt`, `SOURCES.md`

**Interfaces:**
- Consumes: nothing from other tasks (pure text → dicts).
- Produces: `parse_trca_report(text: str, report_url: str | None = None) -> list[dict]` where each dict is `{"native_ref": str, "title": str | None, "winners": list[tuple[str | None, str | None]], "bidders": list[str], "report_url": str | None}` — winners are `(supplier_name, dollar_token)` pairs. Task 5 consumes this exact shape.

- [ ] **Step 1: Write the failing tests**

```python
# scrapers/tests/test_trca_reports.py
import pathlib

from toronto_bids.sources.trca_board import parse_trca_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_armour_stone_refs_and_title():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    refs = {i["native_ref"] for i in items}
    assert refs == {"10039751", "10039753"}   # one row per ref (multi-ref item)
    assert all("ARMOUR" in i["title"].upper() for i in items)


def test_armour_stone_winners_and_amounts():
    items = {i["native_ref"]: i for i in parse_trca_report(_read("trca_armour_stone_2023.txt"))}
    w751 = dict(items["10039751"]["winners"])
    assert '1035477 Ontario Ltd. ("Glenn Windrem Trucking")' in w751 or \
           any("Glenn Windrem" in k for k in w751)
    assert "$1,193,040" in w751.values()
    w753 = dict(items["10039753"]["winners"])
    assert any("Gott Natural Stone" in k for k in w753)
    assert "$567,648" in w753.values()


def test_armour_stone_bidder_list_is_clean_bullets():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    bidders = items[0]["bidders"]
    assert len(bidders) == 4
    assert "H.R. Doornekamp Construction Ltd." in bidders
    assert "Metric Contracting Services Corporation" in bidders
    # The fused results table must NOT be mined: no bidder is a mangled wrap fragment.
    assert all(len(b) > 5 and not b.startswith("$") for b in bidders)


def test_vor_report_names_both_winners_without_amounts():
    items = parse_trca_report(_read("trca_vor_appraisal_2021.txt"))
    assert len(items) == 1 and items[0]["native_ref"] == "10036307"
    names = [w[0] for w in items[0]["winners"]]
    assert any("D. Bottero" in n for n in names)
    assert any("Newmark Knight Frank" in n for n in names)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_trca_reports.py -v`
Expected: FAIL — `ModuleNotFoundError: toronto_bids.sources.trca_board`.

- [ ] **Step 3: Implement the parser**

```python
# scrapers/toronto_bids/sources/trca_board.py
"""TRCA board/executive report parsing (#135). Pure over pdftotext -layout text.

The RECOMMENDATION block is the reliable structure: 'RFT No. <ref> ... be awarded to
<winner> at a total cost not to exceed $<amount>'. The results TABLE is fused multi-line
pdftotext output (names wrap beside prices — the #83 trap) and is never mined; the
bidder LIST comes from the clean '•' bullets after 'received from the following
Proponent(s)'.
"""
import re

# 'RFP No. 10036307' / 'RFT No. 10039751, 10039753' — match the ref shape (8 digits),
# never the label vocabulary (call_number lesson: labels vary, shapes don't).
_REFS = re.compile(r"\bR[FQ][TPQ]\s*No\.?\s*((?:\d{8}(?:\s*,\s*)?)+)")
_REF = re.compile(r"\d{8}")
_TITLE = re.compile(r"^RE:\s*(.+?)(?=^\S|\Z)", re.M | re.S)
# One award clause: ref ... awarded to WINNER at a total cost not to exceed $AMOUNT.
_AWARD = re.compile(
    r"(?:RFT|RFP|RFQ|Contract)\s*No\.?\s*(\d{8})[^$]*?be\s+awarded\s+to\s+"
    r"(.+?)\s+at\s+a\s+total\s+cost\s+not\s+to\s+exceed\s+(\$[\d,]+(?:\.\d{2})?)",
    re.S)
# VOR shape: 'establish a Vendor of Record (VOR) arrangement with A and B for ...'
_VOR = re.compile(r"arrangement\s+with\s+(.+?)\s+for\s+the\s+supply", re.S)
_BULLETS_HEAD = re.compile(r"received\s+from\s+the\s+following\s+Proponent", re.I)
_BULLET = re.compile(r"^\s*[••]\s*(.+?)\s*$", re.M)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fix_quotes(name: str) -> str:
    return name.replace("“", '"').replace("”", '"').replace("’", "'")


def _bullet_names(text: str) -> list[str]:
    m = _BULLETS_HEAD.search(text)
    if not m:
        return []
    names, tail = [], text[m.end():m.end() + 2000]
    lines = tail.splitlines()
    current = None
    for line in lines:
        b = _BULLET.match(line)
        if b:
            if current:
                names.append(_fix_quotes(_squash(current)))
            current = b.group(1)
        elif current is not None:
            cont = line.strip()
            # A wrapped bullet is an indented continuation; anything else ends the list.
            if cont and line.startswith((" ", "\t")):
                current += " " + cont
            else:
                break
    if current:
        names.append(_fix_quotes(_squash(current)))
    return names


def parse_trca_report(text: str, report_url: str | None = None) -> list[dict]:
    refs_m = _REFS.search(text)
    if not refs_m:
        return []
    refs = _REF.findall(refs_m.group(1))
    title_m = _TITLE.search(text)
    title = _squash(title_m.group(1)) if title_m else None
    bidders = _bullet_names(text)

    winners_by_ref: dict[str, list] = {r: [] for r in refs}
    for ref, winner, amount in _AWARD.findall(text):
        if ref in winners_by_ref:
            entry = (_fix_quotes(_squash(winner)), amount)
            if entry not in winners_by_ref[ref]:
                winners_by_ref[ref].append(entry)

    # VOR shape: several winners joined by 'and', no per-winner amounts.
    if not any(winners_by_ref.values()):
        vor = _VOR.search(text)
        if vor:
            names = re.split(r"\s+and\s+", _squash(vor.group(1)))
            for ref in refs:
                winners_by_ref[ref] = [(n.strip(), None) for n in names if n.strip()]

    return [{"native_ref": ref, "title": title, "winners": winners_by_ref[ref],
             "bidders": bidders, "report_url": report_url} for ref in refs]
```

- [ ] **Step 4: Run the tests; iterate on the parser (not the assertions) until green**

Run: `cd scrapers && uv run pytest tests/test_trca_reports.py -v`
Expected: PASS. The assertions encode the fixtures' ground truth — if one fails, the parser is wrong, not the test. The bullet-continuation logic is the likely first failure: debug against the actual fixture bytes, keeping the rule "a wrapped bullet is indented continuation; anything else ends the list".

- [ ] **Step 5: Commit (fixtures ride along)**

```bash
git add scrapers/toronto_bids/sources/trca_board.py scrapers/tests/test_trca_reports.py \
        scrapers/tests/fixtures/agencies/
git commit -m "feat(agencies): TRCA board-report parser — refs, winners, clean bidder list (#135)"
```

---

### Task 4: TRCA discovery + download (fetch half) and storage

**Files:**
- Modify: `scrapers/toronto_bids/sources/trca_board.py` (append fetch/store half)
- Modify: `scrapers/toronto_bids/config.py` (append)
- Test: `scrapers/tests/test_trca_reports.py` (append — storage is tested against an in-memory DB; the HTML walkers against recorded fixtures)

**Interfaces:**
- Consumes: `parse_trca_report` (Task 3), Task 1 models, `HttpClient` (`http.get(url)` returns a `requests`-style response with `.text`/`.content`).
- Produces: `escribe_document_urls(html: str) -> list[str]` (absolute FileStream URLs), `download_reports(conn, http, log) -> int`, `store_trca_reports(conn, buyer_id) -> dict` with keys `solicitations/awards/bids`. Task 7 calls `download_reports` + `store_trca_reports`.

- [ ] **Step 1: Record listing fixtures (one-time, TRCA's own hosting — allowed)**

```bash
cd scrapers/tests/fixtures/agencies
curl -sL -A "Mozilla/5.0" "https://pub-trca.escribemeetings.com/?FillWidth=1&Year=2023" -o trca_escribe_2023.html
wc -c trca_escribe_2023.html   # sanity: non-trivial size
grep -c -i "Meeting.aspx" trca_escribe_2023.html   # expect > 0
```

If `Meeting.aspx` count is 0, the year-list URL shape differs: fetch `https://pub-trca.escribemeetings.com/` instead, save as the fixture, and adjust the test's expected counts to what the real page contains. Record what you saved in `SOURCES.md`.

- [ ] **Step 2: Write the failing tests**

```python
def test_escribe_document_urls_extracts_filestream_links():
    html = _read("trca_escribe_2023.html")
    urls = escribe_document_urls(html)
    assert urls, "expected at least one FileStream/Meeting link in the recorded page"
    assert all(u.startswith("https://pub-trca.escribemeetings.com/") for u in urls)


def test_store_trca_reports_lands_rows(conn):
    ids = seed_buyers(conn)
    text = _read("trca_armour_stone_2023.txt")
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) VALUES (?, 'agency_board', 'x', ?)",
        ("https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809", text))
    got = store_trca_reports(conn, ids["trca"])
    assert got["solicitations"] == 2         # 10039751 + 10039753
    assert got["awards"] >= 2                # one winner each, with amounts
    assert got["bids"] == 8                  # 4 bidders x 2 refs
    row = conn.execute("SELECT award_amount_numeric FROM agency_award "
                       "WHERE native_ref='10039751'").fetchone()
    assert row[0] == 1193040.0
```

(`conn`, `seed_buyers`, `_read` imported/shared from the existing test files.)

- [ ] **Step 3: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_trca_reports.py -v`
Expected: the two new tests FAIL (`escribe_document_urls` / `store_trca_reports` undefined).

- [ ] **Step 4: Implement fetch/store half**

Append to `config.py`:

```python
# TRCA meeting records (#135): current record on eSCRIBE, back-catalogue agenda packages
# on TRCA's Laserfiche. Both are TRCA's own hosting (open-data licence) — NOT the
# bids&tenders portal, which stays gated.
TRCA_ESCRIBE_BASE = "https://pub-trca.escribemeetings.com/"
TRCA_REPORTS_DIR = DATA_DIR / "agencies" / "trca"
# eSCRIBE year pages to walk; range() endpoint updated by whoever runs it in 2027 — moot
# then anyway (Bill 97 amalgamates TRCA 2027-02-01).
TRCA_ESCRIBE_YEARS = range(2019, 2028)
```

Append to `trca_board.py`:

```python
import hashlib
import pathlib
import subprocess

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation, BackgroundPdf
from toronto_bids.store import db

_FILESTREAM = re.compile(r"""(?:href|src)=["']([^"']*[Ff]ile[Ss]tream\.ashx\?DocumentId=\d+[^"']*)""")
_MEETING = re.compile(r"""href=["']([^"']*Meeting\.aspx\?[^"']+)""")


def _absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return config.TRCA_ESCRIBE_BASE.rstrip("/") + "/" + url.lstrip("/").replace("&amp;", "&")


def escribe_document_urls(html: str) -> list[str]:
    """Every FileStream + Meeting link on a page, absolute, order-preserving, deduped."""
    seen, out = set(), []
    for m in (_FILESTREAM.findall(html) + _MEETING.findall(html)):
        u = _absolute(m.replace("&amp;", "&"))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def download_reports(conn, http, log=lambda _m: None) -> int:
    """Walk eSCRIBE year pages -> meeting pages -> FileStream PDFs. Resumable.

    Queue keys on sha256 IS NULL (#96): the hash records that we hold the bytes; text
    records whether pdftotext could read them. Never re-download for unreadable text.
    """
    config.TRCA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # 1. Index: discover FileStream URLs and upsert a background_pdf row per document.
    for year in config.TRCA_ESCRIBE_YEARS:
        page = http.get(config.TRCA_ESCRIBE_BASE, params={"FillWidth": 1, "Year": year}).text
        meeting_urls = [u for u in escribe_document_urls(page) if "Meeting.aspx" in u]
        log(f"  trca {year}: {len(meeting_urls)} meetings")
        for murl in meeting_urls:
            mhtml = http.get(murl).text
            for durl in escribe_document_urls(mhtml):
                if "ashx" in durl.lower():
                    db.upsert_row(conn, BackgroundPdf(url=durl, kind="agency_board"),
                                  overwrite=False)
        conn.commit()
    # 2. Fetch: everything indexed but not yet held.
    n = 0
    for row in conn.execute("SELECT id, url FROM background_pdf "
                            "WHERE kind='agency_board' AND url LIKE '%escribemeetings%' "
                            "AND sha256 IS NULL ORDER BY id").fetchall():
        blob = http.get(row["url"]).content
        if not blob.startswith(b"%PDF"):
            continue                       # HTML error page; leave queued
        sha = hashlib.sha256(blob).hexdigest()
        path = config.TRCA_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        text = _pdftotext(path)
        conn.execute("UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
                     (sha, str(path), text, row["id"]))
        conn.commit()
        n += 1
        log(f"  trca report {n}: {row['url']}")
    return n


def _pdftotext(path: pathlib.Path) -> str | None:
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def store_trca_reports(conn, buyer_id: int) -> dict:
    """Parse every held agency_board report into agency_* rows. Offline, idempotent."""
    counts = {"solicitations": 0, "awards": 0, "bids": 0}
    for row in conn.execute("SELECT url, text FROM background_pdf "
                            "WHERE kind='agency_board' AND text IS NOT NULL "
                            "AND url LIKE '%escribemeetings%' ORDER BY url").fetchall():
        for item in parse_trca_report(row["text"], report_url=row["url"]):
            db.upsert_row(conn, AgencySolicitation(
                buyer_id=buyer_id, native_ref=item["native_ref"], title=item["title"],
                status="awarded" if item["winners"] else None,
                posted_date=None, closing_date=None, portal_url=None,
                source="trca_board"), overwrite=False)
            counts["solicitations"] += 1
            for winner, amount in item["winners"]:
                db.upsert_row(conn, AgencyAward(
                    buyer_id=buyer_id, native_ref=item["native_ref"],
                    supplier_name_raw=winner, award_amount=amount,
                    value_confidential=0, award_date=None,
                    report_url=item["report_url"], source="trca_board"), overwrite=True)
                counts["awards"] += 1
            for bidder in item["bidders"]:
                db.upsert_row(conn, AgencyBid(
                    buyer_id=buyer_id, native_ref=item["native_ref"],
                    bidder_name_raw=bidder, bid_price=None,
                    report_url=item["report_url"], source="trca_board"), overwrite=True)
                counts["bids"] += 1
    conn.commit()
    return counts
```

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_trca_reports.py tests/test_agencies.py -v`
Expected: all PASS (network functions untested by design — fixtures cover the parsers).

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/trca_board.py scrapers/toronto_bids/config.py \
        scrapers/tests/test_trca_reports.py scrapers/tests/fixtures/agencies/
git commit -m "feat(agencies): TRCA eSCRIBE walker + report storage, sha256-queued (#135)"
```

---

### Task 5: Zoo ZB discovery — generalize the TMMIS prober

**Files:**
- Modify: `scrapers/toronto_bids/sources/bid_award_panel.py:203` (`discover_meetings` gains `term_starts=None`) and `:304` (`scrape_agendas` gains and forwards `term_starts`)
- Create: `scrapers/toronto_bids/sources/zoo_board.py` (discovery + download half)
- Modify: `scrapers/toronto_bids/config.py` (append `ZOO_AGENDAS_DIR = DATA_DIR / "agencies" / "zoo" / "agendas"` and `ZOO_REPORTS_DIR = DATA_DIR / "agencies" / "zoo"`)
- Test: `scrapers/tests/test_zoo_reports.py`

**Interfaces:**
- Consumes: `discover_meetings(fetch, log, max_per_term, stop_after_misses, term_starts)`, `cached_agendas(dir)`, `parse_agenda_pdfs(html, meeting)` from `bid_award_panel` (reused, not copied); `BackgroundPdf` model.
- Produces: `ZB_TERM_STARTS`, `scrape_zb_agendas(virtual_display, log) -> dict`, `download_zoo_reports(conn, http, agendas, log) -> int`. Task 7 consumes these.

- [ ] **Step 1: Write the failing test**

```python
# scrapers/tests/test_zoo_reports.py
from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.zoo_board import ZB_TERM_STARTS


def test_discover_meetings_accepts_custom_term_starts():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return "Agenda not found"          # every probe misses

    found = discover_meetings(fetch, term_starts=[("ZB", 2019, "2018-2022", 1)],
                              stop_after_misses=2)
    assert found == {}
    assert calls[0] == "2019.ZB1"          # probes the ZB series, not BA/BD


def test_zb_terms_cover_the_evidenced_years():
    series = {t[0] for t in ZB_TERM_STARTS}
    years = {t[1] for t in ZB_TERM_STARTS}
    assert series == {"ZB"}
    assert 2019 in years and 2023 in years   # ZB1.06 is 2019; redpanda is 2025
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_zoo_reports.py -v`
Expected: FAIL — `discover_meetings() got an unexpected keyword argument 'term_starts'`.

- [ ] **Step 3: Implement**

In `bid_award_panel.py`, change the two signatures (behaviour-preserving default):

```python
def discover_meetings(fetch, log=lambda _m: None, max_per_term=260, stop_after_misses=4,
                      term_starts=None):
    ...
    for series, start_year, term, first_n in (term_starts or TERM_STARTS):
```

```python
def scrape_agendas(agenda_dir, virtual_display: bool = False, log=lambda _m: None,
                   term_starts=None) -> dict:
    ...
        return discover_meetings(fetch, log=log, term_starts=term_starts)
```

Create `zoo_board.py` (discovery half):

```python
"""Toronto Zoo Board of Management (#135): the ZB committee on TMMIS.

Same infrastructure as the Bid Award Panel (#68): TMMIS agendas need the headed browser,
report PDFs are plain-HTTP legdocs (e.g. /legdocs/mmis/2025/zb/bgrd/backgroundfile-N.pdf).
Reuses bid_award_panel's prober — references cannot be derived, so probe-and-confirm.
"""
import hashlib

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (cached_agendas, parse_agenda_pdfs,
                                                  scrape_agendas)
from toronto_bids.store import db

# The Zoo board's TMMIS record is evidenced from 2019 (ZB1.06, 2019-01-28); the 2014-2018
# probe is cheap insurance (4 misses) in case earlier meetings exist.
ZB_TERM_STARTS = [
    ("ZB", 2015, "2014-2018", 1),
    ("ZB", 2019, "2018-2022", 1),
    ("ZB", 2023, "2022-2026", 1),
]


def scrape_zb_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(config.ZOO_AGENDAS_DIR, virtual_display=virtual_display,
                          log=log, term_starts=ZB_TERM_STARTS)


def cached_zb_agendas() -> dict:
    return cached_agendas(config.ZOO_AGENDAS_DIR)


def download_zoo_reports(conn, http, agendas: dict, log=lambda _m: None) -> int:
    """Index every bgrd PDF the ZB agendas link, then fetch the ones not yet held.

    Plain HTTP (legdocs is not Akamai-gated). Queue on sha256 IS NULL (#96).
    """
    from toronto_bids.sources.trca_board import _pdftotext   # same text extraction
    config.ZOO_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind="agency_board"), overwrite=False)
    conn.commit()
    n = 0
    for row in conn.execute("SELECT id, url FROM background_pdf "
                            "WHERE kind='agency_board' AND url LIKE '%/zb/%' "
                            "AND sha256 IS NULL ORDER BY id").fetchall():
        blob = http.get(row["url"]).content
        if not blob.startswith(b"%PDF"):
            continue
        sha = hashlib.sha256(blob).hexdigest()
        path = config.ZOO_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        conn.execute("UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
                     (sha, str(path), _pdftotext(path), row["id"]))
        conn.commit()
        n += 1
        log(f"  zoo report {n}: {row['url']}")
    return n
```

Note: verify `parse_agenda_pdfs` returns dicts with `url` and `reference` keys (read `bid_award_panel.py:329` onward before wiring; if the keys differ, adapt this call site — not the shared function).

- [ ] **Step 4: Run tests**

Run: `cd scrapers && uv run pytest tests/test_zoo_reports.py tests/test_odata.py -v` then `uv run pytest`
Expected: PASS, full suite green (the `term_starts` default preserves BA/BD behaviour).

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/bid_award_panel.py scrapers/toronto_bids/sources/zoo_board.py \
        scrapers/toronto_bids/config.py scrapers/tests/test_zoo_reports.py
git commit -m "feat(agencies): ZB-series discovery via generalized TMMIS prober (#135)"
```

---

### Task 6: Zoo report parser (pure) + storage

**Files:**
- Modify: `scrapers/toronto_bids/sources/zoo_board.py` (append)
- Test: `scrapers/tests/test_zoo_reports.py` (append)
- Fixtures (already in tree): `zoo_energy_retrofit_2019.txt`, `zoo_red_panda_2025.txt`, `zoo_perimeter_fence_2025.txt`

**Interfaces:**
- Consumes: Task 1 models; fixtures.
- Produces: `parse_zoo_report(text, fallback_ref, report_url=None) -> dict | None` returning `{"native_ref", "title", "winner", "amount", "confidential", "report_url"}`; `store_zoo_reports(conn, buyer_id) -> dict` (`solicitations/awards`). Task 7 consumes both.

- [ ] **Step 1: Write the failing tests**

```python
import pathlib

from toronto_bids.sources.zoo_board import parse_zoo_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_energy_retrofit_names_public_winner():
    got = parse_zoo_report(_read("zoo_energy_retrofit_2019.txt"), fallback_ref="2019.ZB1.6")
    assert got["native_ref"] == "RFP 18 (2018-03)"
    assert got["confidential"] == 0
    assert "Ecosystem" in got["winner"]


def test_red_panda_is_confidential_award():
    got = parse_zoo_report(_read("zoo_red_panda_2025.txt"), fallback_ref="2025.ZB15.3")
    assert got["confidential"] == 1
    assert got["amount"] is None            # value withheld, not unpublished
    assert got["native_ref"] == "RFP38"     # the report writes it unspaced


def test_fallback_ref_when_report_names_none():
    text = ("REPORT FOR ACTION WITH\nCONFIDENTIAL ATTACHMENT\n"
            "Subject: Widget Tender Award\n"
            "This report recommends the award of the widget contract.")
    got = parse_zoo_report(text, fallback_ref="2025.ZB9.1")
    assert got["native_ref"] == "2025.ZB9.1"


def test_perimeter_fence_extracts_rft_ref():
    got = parse_zoo_report(_read("zoo_perimeter_fence_2025.txt"), fallback_ref="2025.ZB17.2")
    assert got["confidential"] == 1
    assert got["native_ref"] == "RFT-42"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_zoo_reports.py -v`
Expected: new tests FAIL — `parse_zoo_report` undefined.

- [ ] **Step 3: Implement**

Append to `zoo_board.py`:

```python
import re

from toronto_bids.models import AgencyAward, AgencySolicitation

_ZOO_REF = re.compile(r"\b(R[FQ][TPQ][\s-]*\d{1,3}(?:\s*\(\d{4}-\d{2}\))?)")
_CONFIDENTIAL = re.compile(r"CONFIDENTIAL\s+ATTACHMENT", re.I)
_ZOO_WINNER = re.compile(
    r"award(?:ed)?\s+(?:of\s+)?(?:the\s+)?[\w\s–-]{0,80}?\s+to\s+"
    r"([A-Z][A-Za-z0-9&.,'’ \-]+?(?:Inc|Ltd|Limited|Corp|Corporation|Company)\.?)")
_ZOO_AMOUNT = re.compile(r"total\s+cost\s+(?:not\s+to\s+exceed\s+)?(\$[\d,]+(?:\.\d{2})?)", re.I)
_SUBJECT = re.compile(r"^(?:Subject:|\s*)(.*(?:Tender|RFT|RFP|Award|Contract).*)$", re.M)


def parse_zoo_report(text: str, fallback_ref: str, report_url: str | None = None) -> dict | None:
    if "award" not in text.lower():
        return None                          # not an award report
    confidential = 1 if _CONFIDENTIAL.search(text) else 0
    ref_m = _ZOO_REF.search(text)
    native_ref = re.sub(r"\s+", " ", ref_m.group(1)).strip() if ref_m else fallback_ref
    winner_m = _ZOO_WINNER.search(text)
    amount_m = None if confidential else _ZOO_AMOUNT.search(text)
    title_m = _SUBJECT.search(text)
    return {
        "native_ref": native_ref,
        "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None,
        "winner": winner_m.group(1).strip() if winner_m else None,
        "amount": amount_m.group(1) if amount_m else None,
        "confidential": confidential,
        "report_url": report_url,
    }


def store_zoo_reports(conn, buyer_id: int) -> dict:
    """Parse held ZB reports into agency rows. A confidential award is recorded as an
    award row with NULL supplier/amount and value_confidential=1 — the award happened;
    the value is withheld, which is itself a fact worth archiving."""
    counts = {"solicitations": 0, "awards": 0}
    for row in conn.execute(
            "SELECT reference, url, text FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%/zb/%' AND text IS NOT NULL ORDER BY url").fetchall():
        got = parse_zoo_report(row["text"], fallback_ref=row["reference"] or row["url"],
                               report_url=row["url"])
        if got is None:
            continue
        db.upsert_row(conn, AgencySolicitation(
            buyer_id=buyer_id, native_ref=got["native_ref"], title=got["title"],
            status="awarded", posted_date=None, closing_date=None, portal_url=None,
            source="zoo_board"), overwrite=False)
        counts["solicitations"] += 1
        db.upsert_row(conn, AgencyAward(
            buyer_id=buyer_id, native_ref=got["native_ref"],
            supplier_name_raw=got["winner"], award_amount=got["amount"],
            value_confidential=got["confidential"], award_date=None,
            report_url=got["report_url"], source="zoo_board"), overwrite=True)
        counts["awards"] += 1
    conn.commit()
    return counts
```

- [ ] **Step 4: Run tests; iterate parser until green**

Run: `cd scrapers && uv run pytest tests/test_zoo_reports.py -v`
Expected: PASS. As in Task 3: assertions encode fixture ground truth; fix the parser, not the tests. (`zoo_energy_retrofit_2019.txt` says "award the Energy Retrofit Project to Ecosystem Services Inc." — the winner regex must survive the em-dash and project words before "to".)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/zoo_board.py scrapers/tests/test_zoo_reports.py
git commit -m "feat(agencies): Zoo ZB report parser — confidential-aware awards (#135)"
```

---

### Task 7: `tb enrich-agencies` CLI

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (parser block + `_cmd_enrich_agencies` + dispatch in `main`)
- Test: `scrapers/tests/test_agencies.py` (append)

**Interfaces:**
- Consumes: `seed_buyers`, `download_reports`/`store_trca_reports` (Task 4), `scrape_zb_agendas`/`cached_zb_agendas`/`download_zoo_reports`/`store_zoo_reports` (Tasks 5-6), `build_supplier_dimension`.
- Produces: `tb enrich-agencies [--only zoo|trca] [--fetch] [--scrape] [--virtual-display]`.

- [ ] **Step 1: Write the failing test**

```python
def test_enrich_agencies_offline_parses_cached(conn, monkeypatch, capsys):
    """Offline default: no network, parses whatever background_pdf already holds."""
    from toronto_bids import cli
    ids = seed_buyers(conn)
    text = (pathlib.Path(__file__).parent / "fixtures" / "agencies"
            / "trca_armour_stone_2023.txt").read_text()
    conn.execute("INSERT INTO background_pdf (url, kind, sha256, text) "
                 "VALUES ('https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809',"
                 " 'agency_board', 'x', ?)", (text,))
    conn.commit()
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(conn, "close", lambda: None)   # cli closes; fixture reuses
    rc = cli.main(["enrich-agencies", "--only", "trca"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "trca" in out and "awards" in out
    assert conn.execute("SELECT COUNT(*) FROM agency_award").fetchone()[0] >= 2
```

(add `import pathlib` at the top of the test file if absent.)

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_agencies.py::test_enrich_agencies_offline_parses_cached -v`
Expected: FAIL — unknown command prints help, rc == 0 but no rows / no "awards" output. (If rc==0 masks it, the row-count assertion still fails.)

- [ ] **Step 3: Implement**

Parser block in `build_parser()`:

```python
    p_ag = sub.add_parser(
        "enrich-agencies",
        help="Capture agency/corporation procurement from board records (#135): TRCA "
             "(eSCRIBE, plain HTTP) and Toronto Zoo (ZB agendas on TMMIS). Offline by "
             "default — parses reports already on disk. NEVER touches the bids&tenders "
             "portal (gated on written permission, see docs/permissions/).")
    p_ag.add_argument("--only", choices=["zoo", "trca"],
                      help="Run one body instead of both")
    p_ag.add_argument("--fetch", action="store_true",
                      help="Plain-HTTP fetching first: TRCA eSCRIBE listings + report PDFs, "
                           "and legdocs PDFs for Zoo agendas already cached")
    p_ag.add_argument("--scrape", action="store_true",
                      help="Discover Zoo ZB agendas on TMMIS first (headed browser, "
                           "council extra; implies --fetch for the Zoo's PDFs)")
    p_ag.add_argument("--virtual-display", action="store_true",
                      help="Run --scrape's headed browser under Xvfb")
```

Command (mirrors `_cmd_nightly`'s isolation discipline — one body failing never stops the other):

```python
def _cmd_enrich_agencies(args) -> int:
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.linking.supplier import build_supplier_dimension

    conn = _open_db()
    out = lambda m: print(m, flush=True)
    failures: list[tuple[str, str]] = []
    try:
        ids = seed_buyers(conn)
        bodies = [args.only] if args.only else ["trca", "zoo"]

        if "trca" in bodies:
            try:
                from toronto_bids.sources.trca_board import download_reports, store_trca_reports
                if args.fetch:
                    http = HttpClient()
                    try:
                        print(f"  trca reports fetched : {download_reports(conn, http, log=out)}")
                    finally:
                        http.close()
                got = store_trca_reports(conn, ids["trca"])
                print(f"  trca stored          : {got['solicitations']} solicitations, "
                      f"{got['awards']} awards, {got['bids']} bids")
            except Exception as exc:
                failures.append(("trca", str(exc)))

        if "zoo" in bodies:
            try:
                from toronto_bids.sources.zoo_board import (
                    cached_zb_agendas, download_zoo_reports, scrape_zb_agendas,
                    store_zoo_reports)
                agendas = (scrape_zb_agendas(virtual_display=args.virtual_display, log=out)
                           if args.scrape else cached_zb_agendas())
                print(f"  zoo ZB agendas       : {len(agendas)}"
                      f" ({'scraped' if args.scrape else 'cached'})")
                if agendas and (args.fetch or args.scrape):
                    http = HttpClient()
                    try:
                        print(f"  zoo reports fetched  : "
                              f"{download_zoo_reports(conn, http, agendas, log=out)}")
                    finally:
                        http.close()
                got = store_zoo_reports(conn, ids["toronto-zoo"])
                print(f"  zoo stored           : {got['solicitations']} solicitations, "
                      f"{got['awards']} awards")
            except Exception as exc:
                failures.append(("zoo", str(exc)))

        try:
            print(f"  suppliers            : {build_supplier_dimension(conn)}")
        except Exception as exc:
            failures.append(("supplier_linking", str(exc)))
    finally:
        conn.close()
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    return 1 if failures else 0
```

Dispatch in `main()`:

```python
    if args.command == "enrich-agencies":
        return _cmd_enrich_agencies(args)
```

- [ ] **Step 4: Run tests**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v` then `uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_agencies.py
git commit -m "feat(agencies): tb enrich-agencies — per-body isolated capture CLI (#135)"
```

---

### Task 8: gated portal source, permission letters, permissions convention

**Files:**
- Modify: `scrapers/toronto_bids/config.py` (append `BIDS_TENDERS_PORTALS`)
- Create: `scrapers/toronto_bids/sources/bids_tenders.py`
- Create: `docs/letters/2026-07-18-toronto-zoo-portal-permission.md`, `docs/letters/2026-07-18-trca-portal-permission.md`, `docs/permissions/README.md`
- Test: `scrapers/tests/test_agencies.py` (append)

**Interfaces:**
- Produces: `config.BIDS_TENDERS_PORTALS`, `bids_tenders.fetch_listings(http, portal)` which raises `PermissionError` while `enabled` is false. The listing parser is deliberately NOT in this plan: it needs recorded fixtures, which need permission — write it when a yes lands.

- [ ] **Step 1: Write the failing test**

```python
def test_portal_source_is_gated():
    import pytest as _pytest
    from toronto_bids import config
    from toronto_bids.sources.bids_tenders import fetch_listings
    assert all(not p["enabled"] for p in config.BIDS_TENDERS_PORTALS), \
        "a portal was enabled without a recorded permission"
    with _pytest.raises(PermissionError):
        fetch_listings(None, config.BIDS_TENDERS_PORTALS[0])
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_agencies.py::test_portal_source_is_gated -v`
Expected: FAIL — module/config missing.

- [ ] **Step 3: Implement config + gate**

Append to `config.py`:

```python
# bids&tenders portals (#135). `enabled` stays False until the BODY's written permission
# is recorded in docs/permissions/ and the flipping commit references it — the PMMD/Ariba
# precedent (#117). The Vendor ToS is clickwrap we have not accepted, and its copyright
# notice is blanket; "settled" means the body said yes, not our reading of their terms.
BIDS_TENDERS_PORTALS = [
    {"slug": "toronto-zoo", "portal_url": "https://torontozoo.bidsandtenders.ca/",
     "enabled": False, "permission": None},
    {"slug": "trca", "portal_url": "https://trca.bidsandtenders.ca/",
     "enabled": False, "permission": None},
]
```

Create `sources/bids_tenders.py`:

```python
"""The bids&tenders portal source (#135) — GATED, and currently a gate only.

Listing capture is written when the first written permission lands in docs/permissions/:
the parser needs recorded fixtures, and recording a fixture means fetching the portal,
which is exactly what the gate forbids until then. Bid DOCUMENTS are out of scope
regardless of permission state — they sit behind the Vendor clickwrap.
"""


def fetch_listings(http, portal: dict):
    if not portal.get("enabled"):
        raise PermissionError(
            f"bids&tenders portal '{portal['slug']}' is not enabled: fetching requires the "
            f"body's written permission recorded in docs/permissions/ (see #135 / #103). "
            f"Current permission record: {portal.get('permission')!r}")
    raise NotImplementedError(
        "Listing capture is unwritten by design — record fixtures under the granted "
        "permission first, then implement normalize() against them (spec 2026-07-18).")
```

- [ ] **Step 4: Write the letters and permissions convention (full text, committed)**

`docs/permissions/README.md`:

```markdown
# Recorded permissions

One file per grant, named `YYYY-MM-DD-<body>.md`, containing the request sent, the reply
verbatim (with sender name/role/date), and what it covers. A `config.py` gate flips only
in a commit that references the file. Precedent: PMMD's written authorization for the
Ariba Respond capture (#117, 2026-07).
```

`docs/letters/2026-07-18-trca-portal-permission.md`:

```markdown
To: procurement@trca.ca
Subject: Permission request — archiving TRCA's public bid listings (civic archive)

Hello,

I maintain toronto-bids (github.com/CivicTechTO/toronto-bids), a volunteer CivicTechTO
project that archives City of Toronto procurement records so the public record stays
available after bids close. It already preserves the City's central feeds, and Toronto
Council pays 62.6% of TRCA's operating levy, so TRCA's procurement record is squarely in
its public-interest scope.

We would like your permission to periodically fetch the PUBLICLY VISIBLE listing
metadata on trca.bidsandtenders.ca — solicitation number, title, status, posted and
closing dates — read-only and rate-limited (a nightly pass, a few dozen requests). We
would not log in, would not download bid documents, and would not collect anything a
member of the public cannot already see. The archived record is published openly with
attribution to TRCA as the source, and we will stop immediately on request.

Two notes for context. First, we already archive TRCA award outcomes from your public
board reports on pub-trca.escribemeetings.com, under TRCA's open-data posture. Second,
Bill 97's amalgamation timeline (2027-02-01) is why we are asking now: after transition,
the TRCA-branded record may no longer exist to preserve.

If someone else (including the platform operator) should field this, I would appreciate
a pointer. Happy to answer anything.

Thank you,
Alex Waolson — CivicTechTO / toronto-bids
```

`docs/letters/2026-07-18-toronto-zoo-portal-permission.md`:

```markdown
To: Toronto Zoo — Purchasing & Supply (confirm exact addressee at torontozoo.com/business)
Subject: Permission request — archiving the Zoo's public bid listings (civic archive)

Hello,

I maintain toronto-bids (github.com/CivicTechTO/toronto-bids), a volunteer CivicTechTO
project that archives City of Toronto procurement records — the City's central feeds,
council award records, and the agencies' public postings — so the record stays available
after bids close. The Toronto Zoo's Bid Opportunities System is one of the last public
posting venues the archive does not preserve.

We would like your permission to periodically fetch the PUBLICLY VISIBLE listing
metadata on torontozoo.bidsandtenders.ca — solicitation number, title, status, posted
and closing dates — read-only and rate-limited (a nightly pass, a few dozen requests).
We would not log in, would not download bid documents, and would not collect anything a
member of the public cannot already see. The archived record is published openly with
attribution, and we will stop immediately on request.

For context: the City's Purchasing & Materials Management Division authorized this
project's archival access to its Ariba postings in writing in July 2026, under the
City's open-by-default policy; we are asking the Zoo the same question for its venue.
We already archive the Board of Management's public award reports from TMMIS.

If someone else should field this, I would appreciate a pointer. Happy to answer
anything.

Thank you,
Alex Waolson — CivicTechTO / toronto-bids
```

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_agencies.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/config.py scrapers/toronto_bids/sources/bids_tenders.py \
        docs/letters/ docs/permissions/ scrapers/tests/test_agencies.py
git commit -m "feat(agencies): gated bids&tenders source + permission letters (#135)"
```

---

### Task 9: export `buyers` section + CLAUDE.md

**Files:**
- Modify: `scrapers/toronto_bids/export/document.py` (new section before the `return`)
- Modify: `CLAUDE.md` (new subsection under Architecture)
- Test: `scrapers/tests/test_agencies.py` (append)

**Interfaces:**
- Consumes: Task 1 tables.
- Produces: `build_export_document(...)["buyers"]` — a list ordered by slug, each `{slug, name, kind, partnered, funding_share, platform, notes, solicitations: [...], awards: [...], bids: [...]}`. Headline `counts` unchanged in meaning (new tables appear as new keys only).

- [ ] **Step 1: Write the failing test**

```python
def test_export_buyers_section(conn):
    from toronto_bids.export.document import build_export_document
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["trca"], native_ref="10039751",
        supplier_name_raw="Gott Natural Stone '99 Inc.", award_amount="$567,648",
        value_confidential=0, award_date=None, report_url=None, source="trca_board"),
        overwrite=True)
    doc = build_export_document(conn, generated_at="2026-07-18T00:00:00+00:00")
    buyers = {b["slug"]: b for b in doc["buyers"]}
    assert set(buyers) == {"toronto-zoo", "trca"}
    assert buyers["trca"]["partnered"] == 1        # consumers can segment
    assert buyers["trca"]["awards"][0]["native_ref"] == "10039751"
    assert buyers["toronto-zoo"]["awards"] == []
    # City-only headline sections keep their meaning: no agency rows leak in.
    assert all("native_ref" not in s for s in doc["solicitations"])
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_agencies.py::test_export_buyers_section -v`
Expected: FAIL — `KeyError: 'buyers'`.

- [ ] **Step 3: Implement**

In `document.py`, before the `return`, add (and add `"buyers": buyers_out` to the returned dict):

```python
    # Agency buyers (#135): a fourth keyspace, in its own section so no City-spine
    # count changes meaning. Partnered buyers carry their flag and funding share so a
    # consumer can segment — the TRCA scope decision is the reader's, made visible.
    buyers_out = []
    for buyer in _rows(conn, "SELECT * FROM buyer ORDER BY slug"):
        bid_ = buyer["id"]
        buyers_out.append(_drop(buyer, "id") | {
            "solicitations": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_solicitation WHERE buyer_id={bid_} "
                      "ORDER BY native_ref")],
            "awards": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_award WHERE buyer_id={bid_} "
                      "ORDER BY native_ref, supplier_name_raw")],
            "bids": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_bid WHERE buyer_id={bid_} "
                      "ORDER BY native_ref, bidder_name_raw")],
        })
```

(`bid_` is an int from our own query — no injection surface; matches the file's `_rows` style.)

- [ ] **Step 4: Update `CLAUDE.md`**

Add under Architecture, after the "Ariba attachments" subsection:

```markdown
### Agency capture (`buyers.py`, `sources/trca_board.py`, `sources/zoo_board.py`, #135) — the fourth keyspace

`tb enrich-agencies`. Agencies/corporations procure outside the PMMD feed (#103); their
records live in `agency_solicitation`/`agency_award`/`agency_bid` keyed
`(buyer_id, native_ref)` — **no join to the City keyspaces is attempted**. The `buyer`
dimension carries `partnered`/`funding_share` (TRCA: 0.626) so exports segment rather
than mix; headline counts stay City-only. Award records come from board reports, not the
portal: TRCA's eSCRIBE (plain HTTP; the results TABLE is fused pdftotext and never
mined — bidders come from the bullet list, winners+amounts from the RECOMMENDATION
clause) and the Zoo's ZB committee on TMMIS (same prober as BA/BD; 2025-era reports
route values to a CONFIDENTIAL ATTACHMENT → `value_confidential=1`, not fake NULLs).
**The bids&tenders portal is never fetched**: `sources/bids_tenders.py` is a gate that
raises until a body's written permission lands in `docs/permissions/` (the PMMD/Ariba
precedent). Bid documents stay out of scope regardless — they sit behind the Vendor
clickwrap. Bill 97 amalgamates TRCA away on 2027-02-01; its capture is deadline-bound.
```

- [ ] **Step 5: Run the full suite**

Run: `cd scrapers && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/export/document.py CLAUDE.md scrapers/tests/test_agencies.py
git commit -m "feat(agencies): export buyers section; document the fourth keyspace (#135)"
```

---

## After the last task

Run the whole suite once more (`cd scrapers && uv run pytest`), then use superpowers:finishing-a-development-branch — the branch also carries the spec commit. The PR closes nothing by itself: #135 stays open for the portal half (letters must be sent by Alex; a recorded yes flips the config gate and unlocks the listing-parser follow-up). A live `tb enrich-agencies --fetch` run against TRCA's eSCRIBE is the real-world verification (`/verify`), expected to yield board-report awards immediately; `--scrape` for the Zoo needs the council extra and a display.
