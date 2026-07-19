# Committee/Council award bid capture (#164 + #167)

**Date:** 2026-07-19
**Status:** approved (autonomous — maintainer reviews at the PR), not yet implemented
**Delivers:** the bid record for large awards decided by Council / a Standing Committee — above the
Bid Award Panel's delegation ceiling, so absent from the panel-agenda corpus the archive mines
today (#84/#94). Folds #167 (2013-2018, ≥$500K) into #164 (2020+, ≥$3M): one committee-report
source, both eras.

## 1. Feasibility (probed live 2026-07-19)

The assumption behind #164 — that committee reports tabulate the bids "the same way Bid Award Panel
reports do" — **holds**, verified against real reports:

- **RFQ/RFP** (District 2 Waste Collection, `Doc4553928310`, $197M): the report has
  `Table 2: Summary of Bids Received including Bid Price` — `GFL Environmental Inc. $36,182,930` /
  `Halton Recycling Ltd. $37,247,071` (the losing bidder, captured).
- **RFT** (Ashbridges tender, $85M): `opened the following bids:` → `Kenaidan $83,658,000 /
  Torbear $88,910,095 / Alberici $95,995,000`.

(An earlier "reports withhold bids" read was WebFetch's small-model misreading the PDF; `pdftotext`
— the archive's own tool — finds the tables cleanly.)

The reports live on **legdocs** (`toronto.ca/legdocs/mmis/<year>/<cmte>/bgrd/backgroundfile-<n>.pdf`,
plain HTTP, not Akamai-gated — the same host `download_reports` already fetches).

## 2. Discovery — mostly plain HTTP

The one gated resource is TMMIS agenda-item pages (403 to plain HTTP). The chain:

1. **doc → (committee, agenda item#): plain HTTP.** The **open-data voting-record CSVs**
   (`members-of-toronto-city-council-voting-record`, one resource per council term) list every
   award item with the document number **in the title** — `Award of Doc<10>`,
   `Award of … Document Number <10>`, `Award of Ariba Document Number <10>`. Parsing those yields
   `{document_number, reference, committee, title}` with no browser. This is the target index.
2. **agenda item# → report PDF URL: headed browser.** For each item, fetch
   `secure.toronto.ca/council/agenda-item.do?item=<ref>` under Xvfb (reuse the council prober),
   scrape the `backgroundfile-<n>.pdf` link whose title is `Award of Doc<n>`. One page per item;
   cached on disk so a re-run never re-drives the browser (like the BA-agenda cache).
3. **report PDF → bid table: plain HTTP + pdftotext.**

## 3. Architecture — `sources/committee_awards.py`

Pure/impure split, mirroring the agency board-report sources (#130/#141):

- **`award_items_from_voting_record(csv_text) -> list[dict]`** (pure): parse one voting-record CSV,
  return distinct `{document_number, reference, committee, title}` for rows whose *Agenda Item Title*
  names a 10-digit document number via `award_doc_number(title)`. Tested against a fixture CSV.
- **`award_doc_number(title) -> str | None`** (pure): extract the 10-digit doc from the title
  vocabulary (`Doc<n>`, `Document Number <n>`, `Ariba Document Number <n>`) — match the number, not
  the words (the #77/#114 lesson).
- **`fetch_voting_records(http) -> list[str]`** (impure): CKAN `package_show` → the per-term CSV
  resource URLs → the CSV bodies. UUIDs resolved at runtime (never hardcoded, #CKAN gotcha).
- **`discover_report_urls(items, cache_dir, *, virtual_display, log) -> dict[ref, url]`** (browser):
  fetch each item page under Xvfb, cache the HTML, scrape the `Award of Doc<n>` background-file URL.
- **`download_committee_reports(conn, http, url_map, log) -> int`** (impure, plain HTTP): fetch each
  report PDF, store it in `background_pdf` (kind=`committee_award`, carrying `document_number` and
  the legdocs URL), `pdftotext` the text. Resilient loop (`_store_pending_pdfs` pattern), sha256-queued.
- **`parse_committee_bids(text) -> list[tuple[str, str]]`** (pure): the bid table. Two shapes:
  - `Summary of Bids Received …` → rows of `<supplier> … <$price>` until a blank/section boundary.
  - `opened the following bids …` → `<Tenderer> … <$Total Tender Price>` rows.
  Read cells/lines positionally; refuse a row without a clean `name … $price`. **Refuse the whole
  report** when no table is present (emergency / sole-source / "only supplier" / confidential
  attachment) — no bids by nature, don't invent them (#130 discipline). `bid_price` verbatim,
  `bid_price_numeric` via `amount.py`; the winner's `*` marker kept.
- **`store_committee_bids(conn, log) -> int`** (impure): for each stored committee-award report,
  parse its text and upsert `Bid` rows with the report's **`document_number`** and
  `source='committee_award'`. Because the award is already in the spine with that document number,
  the bids attach **directly** — no bridge, no fuzzy match, no false-merge surface (the opposite of
  #124's hard part).

### CLI

`tb enrich-committee-awards [--scrape] [--virtual-display]`:
- default (offline): parse cached item pages + stored reports → attach bids.
- `--scrape`: refresh the voting-record index + browser-discover report URLs + download reports.

On-demand, **not on the nightly path** (browser-bound), like `enrich-titles --scrape` was and the
agency scrapers are.

## 4. Data flow

voting-record CSV (plain HTTP) → `award_items_from_voting_record` → `discover_report_urls`
(browser, cached) → `download_committee_reports` (plain HTTP + pdftotext) → `parse_committee_bids`
(pure) → `store_committee_bids` (Bid rows keyed on document_number, source='committee_award') →
`build_supplier_dimension` picks up new bidders → export nests them under the solicitation (they
carry a document_number, exactly like #114 Award Summary bids — no export change).

## 5. Scope / target set

- Award items from the voting-record whose title names a document number AND whose solicitation is
  in the spine with **no captured bids** (the ~99 modern ≥$3M + ~266 older ≥$500K, refreshed by
  query). Parsing every award item is fine too — a bid table found is a bid table stored; the
  no-captured-bids filter just bounds the browser discovery.
- **Both eras** (#164 + #167) — one source; #167 closes as folded here.

## 6. Refusal / genuine-zero (load-bearing)

Some large awards genuinely have no bid table: emergency, sole-source, "only supplier",
non-competitive, or a value routed to a confidential attachment (MFIPPA). `parse_committee_bids`
returns `[]` for those and `store_committee_bids` writes nothing — the award stays honestly
zero-bid. Validate the refusal against real negatives (an emergency/sole-source report) per the
#130 discipline, so a report's prose ("$X to the only supplier") is never mistaken for a bid.

## 7. Testing

- **Pure parsers** against real report text captured as fixtures: the RFQ `Summary of Bids Received`
  table (District 2 — 2 bidders), the RFT `opened the following bids` table (Ashbridges — 3
  bidders), `award_doc_number` across the title vocabulary, `award_items_from_voting_record` against
  a fixture CSV, and **negatives** (an emergency/sole-source report → `[]`).
- **`store_committee_bids`**: bids attach to the right solicitation by document_number; `bid_price_numeric`
  parsed; the winner marker kept; a no-table report stores nothing.
- **Browser discovery** stays untested by unit tests (as with the council/agency scrapers).
- **Mandatory live run** (#136/#138): a real `--scrape` over a sample of the target awards; report
  how many report URLs discovered, how many reports carried a parseable bid table vs refused, how
  many bids attached, and the drop in the zero-bid count. The offline fixtures are a starting point.

## 8. Out of scope

- The pre-Ariba unique-match bridge (#124/#165 — different mechanism; this is exact by document
  number).
- Non-award committee reports (status updates, budgets) — the parser refuses them by finding no bid
  table.
- Full-text/OCR of the reports beyond the bid table.
- Putting the browser discovery on the nightly path (browser-bound, on-demand only).

## 9. Recording

On completion, comment on #164 with the measured recovery (report URLs found, bid tables parsed vs
refused, bids attached, zero-bid drop) and **close #167 as folded in**. Update #163's accounting.
