# Committee/Council Award Bid Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the bid tables for large awards decided by Council / a Standing Committee (above the Bid Award Panel threshold) from their staff reports on legdocs, attaching the bids directly to their spine solicitations by document number.

**Architecture:** A new `sources/committee_awards.py`. The target index comes from the open-data voting-record CSVs (plain HTTP — item titles carry the doc number). Report URLs come from TMMIS agenda-item pages (headed browser, cached). Report PDFs are plain HTTP + `pdftotext`; a pure parser reads the two bid-table shapes; bids store with the report's `document_number` (source='committee_award') and attach directly like #114 Award Summary bids — no bridge, no fuzzy match.

**Tech Stack:** Python 3.12, `uv`, pytest (offline, fixture-based). Playwright/Xvfb for discovery only. `pdftotext` (poppler).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-committee-award-bids-design.md`.
- **No lint/typecheck** — only `uv run pytest` from `scrapers/`.
- **Refuse rather than invent** (#130): a report with no bid table (emergency/sole-source/confidential attachment) stores NO bids; the award stays honestly zero-bid.
- **Match the number, not the vocabulary** (#77/#114): extract the 10-digit doc from `Doc<n>` / `Document Number <n>` / `Ariba Document Number <n>`.
- **Bids attach by document_number directly** — these awards are already in the spine, so no linking pass, no false-merge surface.
- **Amounts**: `bid_price` verbatim (keep the winner `*`), `bid_price_numeric` via `amount.py`; never aggregate the raw string.
- Browser discovery is **on-demand, not nightly** (reuse the `council` extra + `--virtual-display`).
- Real fixtures already captured: `scrapers/tests/fixtures/committee/district2_rfq_doc4553928310.txt` (RFQ, `Summary of Bids Received`, 2 bidders) and `ashbridges_tender_2010.txt` (RFT, `opened the following bids`, 3 bidders).
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE
  ```
- Branch `feat-164-committee-award-bids` (already checked out). Do not commit to `main`.

## File Structure

- `scrapers/toronto_bids/sources/committee_awards.py` — the whole source (Tasks 1-4).
- `scrapers/toronto_bids/store/schema.sql` — `background_pdf.kind='committee_award'` needs no schema change (kind is free text); confirm.
- `scrapers/toronto_bids/cli.py` — `tb enrich-committee-awards` (Task 4).
- `scrapers/tests/test_committee_awards.py` — tests (Tasks 1-3).

---

## Task 1: Voting-record target index (pure + CKAN fetch)

**Files:** Create `scrapers/toronto_bids/sources/committee_awards.py`; Test `scrapers/tests/test_committee_awards.py`; fixture `scrapers/tests/fixtures/committee/voting_record_sample.csv`.

**Interfaces:**
- `award_doc_number(title: str) -> str | None` — the 10-digit doc from an item title, else None.
- `award_items_from_voting_record(csv_text: str) -> list[dict]` — distinct `{document_number, reference, committee, title}` for award rows.
- `fetch_voting_records(http) -> list[str]` — the per-term CSV bodies from CKAN.

- [ ] **Step 1: Create the fixture** `scrapers/tests/fixtures/committee/voting_record_sample.csv` (real header + a few rows, incl. the three title vocabularies and a non-award row):

```csv
_id,Term,First Name,Last Name,Committee,Date/Time,Agenda Item #,Agenda Item Title,Motion Type,Vote,Result,Vote Description
1,2022-2026,A,B,City Council,2024-05-01 10:00 AM,2024.GG16.12,"Award of Doc4553928310 to GFL Environmental Inc., for District 2 Waste Collection",Adopt,Yes,Carried,x
2,2022-2026,A,B,City Council,2024-05-01 10:00 AM,2024.GG16.12,"Award of Doc4553928310 to GFL Environmental Inc., for District 2 Waste Collection",Adopt,Yes,Carried,x
3,2022-2026,C,D,City Council,2024-06-01 10:00 AM,2024.GG14.8,Process for Award of Negotiable Request for Proposal Document Number 4053424337 for Ferry Vessels,Adopt,Yes,Carried,x
4,2022-2026,C,D,General Government Committee,2023-03-01 10:00 AM,2023.GG2.11,Award of Ariba Document Number 3448368603 to CH2M Hill Canada for Basement Flooding,Adopt,Yes,Carried,x
5,2022-2026,C,D,City Council,2024-06-01 10:00 AM,2024.MM1.1,Election of the Speaker,Adopt,Yes,Carried,x
```

- [ ] **Step 2: Write failing tests** in `tests/test_committee_awards.py`:

```python
from pathlib import Path
from toronto_bids.sources.committee_awards import award_doc_number, award_items_from_voting_record

FIX = Path(__file__).parent / "fixtures" / "committee"


def test_award_doc_number_reads_the_title_vocabulary():
    assert award_doc_number("Award of Doc4553928310 to GFL") == "4553928310"
    assert award_doc_number("Process for Award of Negotiable RFP Document Number 4053424337 for x") == "4053424337"
    assert award_doc_number("Award of Ariba Document Number 3448368603 to CH2M") == "3448368603"
    assert award_doc_number("Election of the Speaker") is None


def test_award_items_from_voting_record_dedups_and_skips_non_awards():
    items = award_items_from_voting_record((FIX / "voting_record_sample.csv").read_text())
    by_doc = {i["document_number"]: i for i in items}
    assert set(by_doc) == {"4553928310", "4053424337", "3448368603"}   # non-award row dropped, dupes merged
    assert by_doc["4553928310"]["reference"] == "2024.GG16.12"
    assert by_doc["4553928310"]["committee"] == "City Council"
```

- [ ] **Step 3: Run — fails** (`cd scrapers && uv run pytest tests/test_committee_awards.py -v`).

- [ ] **Step 4: Implement** `committee_awards.py`:

```python
import csv
import io
import re

_DOC = re.compile(r"(?:Ariba\s+)?Doc(?:ument)?(?:\s*Number)?\s*[:#]?\s*(\d{10})", re.IGNORECASE)


def award_doc_number(title):
    """The 10-digit document number an award item names, else None. Match the number, not the
    vocabulary — titles say 'Doc<n>', 'Document Number <n>', 'Ariba Document Number <n>'."""
    if not title:
        return None
    m = _DOC.search(title)
    return m.group(1) if m else None


def award_items_from_voting_record(csv_text):
    """Distinct award items carrying a document number, from one voting-record CSV.
    Returns [{document_number, reference, committee, title}]. Dedups the per-member vote rows."""
    out = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        title = row.get("Agenda Item Title") or ""
        doc = award_doc_number(title)
        if not doc:
            continue
        out.setdefault(doc, {"document_number": doc, "reference": row.get("Agenda Item #"),
                             "committee": row.get("Committee"), "title": title})
    return list(out.values())


def fetch_voting_records(http):
    """The per-term voting-record CSV bodies from CKAN (UUIDs resolved at runtime, never hardcoded)."""
    import json
    base = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action"
    pkg = json.loads(http.get(f"{base}/package_show?id=members-of-toronto-city-council-voting-record").text)
    csvs = []
    for r in pkg["result"]["resources"]:
        if r.get("format", "").upper() == "CSV" and r.get("name", "").startswith("member-voting-record-2"):
            csvs.append(http.get(r["url"]).text)
    return csvs
```

(Verify `HttpClient.get(url).text` is the right accessor — read `toronto_bids/http.py`; adjust if it's `.get(url)` returning bytes/other.)

- [ ] **Step 5: Run tests + full suite; Commit** (`git add` the source, test, fixture; message: `feat(committee): voting-record award-item index (#164)`).

---

## Task 2: `parse_committee_bids` — the two-format bid-table parser

**Files:** Modify `committee_awards.py`; Test `tests/test_committee_awards.py`.

**Interfaces:** `parse_committee_bids(text: str) -> list[tuple[str, str]]` — `(bidder, bid_price)` per bid; `[]` when no table (refuse).

- [ ] **Step 1: Failing tests** (against the REAL captured fixtures):

```python
from toronto_bids.sources.committee_awards import parse_committee_bids

def test_parse_rfq_summary_of_bids_received():
    bids = parse_committee_bids((FIX / "district2_rfq_doc4553928310.txt").read_text())
    names = {b for b, _ in bids}
    assert "GFL Environmental Inc." in names
    assert any("Halton Recycling" in b for b, _ in bids)      # the losing bidder captured
    assert len(bids) == 2

def test_parse_rft_opened_the_following_bids():
    bids = parse_committee_bids((FIX / "ashbridges_tender_2010.txt").read_text())
    names = {b for b, _ in bids}
    assert any("Kenaidan" in b for b in names)
    assert any("Torbear" in b for b in names)
    assert any("Alberici" in b for b in names)
    assert len(bids) == 3

def test_parse_refuses_a_report_with_no_bid_table():
    assert parse_committee_bids("... the only supplier able to provide ... emergency ...") == []
```

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement `parse_committee_bids`.** Read both fixture .txt files in full first to see the exact layout. Anchor on the two table headers:
  - `Summary of Bids Received` (RFQ/RFP): the rows below pair a supplier name with the first `$amount` on/near its line, until a blank-line gap or a footnote (`*…`) / next section. Strip the winner `*`. Skip the column-header line(s) (`Supplier Name`, `Bid Price…`).
  - `opened the following bids` (RFT): the `Tenderer … Total Tender Price` table; each row is `<name> … <$amount>`.
  Use a shared line-walk that, after either header, reads `<name> … <$price>` rows and stops at the first line with no `$amount` after a name (the #94/#116 "read cells, refuse an unequal/blank row" discipline). Return `[]` if neither header is present. `parse_amount` (from `toronto_bids.amount`) validates the price; keep the raw `$` string as `bid_price`.

- [ ] **Step 4: Run tests + full suite; Commit** (`feat(committee): parse_committee_bids for both report bid-table shapes (#164)`).

---

## Task 3: Store — download reports + attach bids by document_number

**Files:** Modify `committee_awards.py`; Test `tests/test_committee_awards.py`.

**Interfaces:**
- `download_committee_reports(conn, http, url_map: dict, log) -> int` — fetch each report PDF (plain HTTP), store in `background_pdf` (kind='committee_award', with `document_number` + url + text via pdftotext). Reuse the resilient download pattern (`trca_board._store_pending_pdfs` or equivalent) — skip a dead URL, `%PDF` guard, sha256-queued.
- `store_committee_bids(conn, log) -> int` — for each `background_pdf WHERE kind='committee_award'`, parse its text with `parse_committee_bids` and upsert `Bid(document_number=<report doc>, reference=None, bidder_name_raw, bid_price, bid_price_numeric, source='committee_award')`. Returns bids stored.

- [ ] **Step 1: Failing test** (store path, no network — seed a background_pdf row with the fixture text):

```python
def test_store_committee_bids_attaches_by_document_number(conn):
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, BackgroundPdf
    from toronto_bids.sources.committee_awards import store_committee_bids
    db.upsert_row(conn, Solicitation(document_number="4553928310", source="odata"), overwrite=True)
    db.upsert_row(conn, BackgroundPdf(url="https://x/backgroundfile-1.pdf", document_number="4553928310",
                  kind="committee_award", text=(FIX / "district2_rfq_doc4553928310.txt").read_text()),
                  overwrite=True)
    conn.commit()
    n = store_committee_bids(conn)
    assert n == 2
    rows = conn.execute("SELECT bidder_name_raw, document_number FROM bid WHERE source='committee_award'").fetchall()
    assert {r[0] for r in rows} >= {"GFL Environmental Inc."}
    assert all(r[1] == "4553928310" for r in rows)
```

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement.** `store_committee_bids` iterates the committee_award reports, parses, and upserts Bid rows via `db.upsert_row`. Confirm `BackgroundPdf` model carries `document_number`, `kind`, `text` (it does — used by #114/#126). Confirm `Bid` upsert with `reference=None, document_number=<doc>` keys cleanly (bid_key COALESCEs both — a committee bid looks like a #114 award-summary bid). `download_committee_reports` mirrors `trca_board.download_reports` (read that first).

- [ ] **Step 4: Run tests + full suite; Commit** (`feat(committee): download reports + attach bids by document_number (#164)`).

---

## Task 4: Browser discovery + CLI

**Files:** Modify `committee_awards.py`, `cli.py`.

**Interfaces:** `discover_report_urls(items, cache_dir, *, virtual_display, log) -> dict[reference, url]`; CLI `tb enrich-committee-awards [--scrape] [--virtual-display]`.

- [ ] **Step 1: Implement `discover_report_urls`** (browser, no unit test — as with council/agency scrapers). Reuse the council/agency headed-browser fetch (see `bid_award_panel.agenda_fetcher` / `sources/council.py`): for each item `reference`, fetch `https://secure.toronto.ca/council/agenda-item.do?item=<reference>`, cache the HTML under `cache_dir`, and scrape the `<a href=…backgroundfile-<n>.pdf>` whose link text contains `Award of Doc<doc>` (fall back to the first `bgrd/backgroundfile` link if only one). Return `{reference: url}`. An item already cached is not refetched.

- [ ] **Step 2: Wire the CLI** `_cmd_enrich_committee_awards`:
  - `--scrape`: `fetch_voting_records(http)` → `award_items_from_voting_record` (all terms) → filter to spine awards with no captured bids → `discover_report_urls(..., virtual_display=args.virtual_display)` → `download_committee_reports` → `store_committee_bids` → `build_supplier_dimension`.
  - default (offline): `store_committee_bids` over already-downloaded reports + `build_supplier_dimension`.
  Add the subparser + dispatch, mirroring `enrich-agencies`.

- [ ] **Step 3: Run full suite; Commit** (`feat(committee): browser discovery + tb enrich-committee-awards (#164)`).

---

## Task 5: Live run + recording

- [ ] **Step 1:** `cd scrapers && TB_DATA_DIR="$HOME/tb-data" uv run tb enrich-committee-awards --scrape --virtual-display` over a bounded sample of the target awards (start with the ≥$10M tier to keep the first browser run small). Capture: report URLs discovered, reports with a parseable bid table vs refused, bids attached, and the zero-bid-count drop.
- [ ] **Step 2:** Comment on #164 with the measured recovery; **close #167 as folded in**; update #163's accounting.

## Self-Review

**Spec coverage:** voting-record index → Task 1; two-format parser + refusal → Task 2; direct-attachment store → Task 3; browser discovery + CLI → Task 4; live gate + recording → Task 5. ✓
**Placeholder scan:** Task 2 Step 3 says "read the fixtures first" — the fixtures are real and committed; the parser shape is described concretely (two anchors + line-walk + refuse). No TBD.
**Type consistency:** `award_doc_number -> str|None`, `award_items_from_voting_record -> list[dict{document_number,reference,committee,title}]`, `parse_committee_bids -> list[(bidder,price)]`, `store_committee_bids -> int`, `discover_report_urls -> dict[ref,url]` — consistent across tasks.
