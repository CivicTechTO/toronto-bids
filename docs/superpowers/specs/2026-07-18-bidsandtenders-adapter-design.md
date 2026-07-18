# bids&tenders capture (#135): Toronto Zoo + TRCA — design

2026-07-18. Implements #135 (bids&tenders adapter), covering #109 (Toronto Zoo) and #132 (TRCA). First consumer of #103's keyspace decision, which is made here and inherited by the MERX (#133) and Bonfire (#134) adapters.

## Decisions (the three gates, settled 2026-07-18)

1. **TRCA scope: capture, flagged partnered.** Toronto pays 62.6% of TRCA's operating levy; Bill 97 amalgamates TRCA into a regional authority on 2027-02-01, so the record is deadline-bound. The buyer dimension carries a `partnered` flag and `funding_share` so exports and aggregates can segment or exclude. The same pattern will frame Waterfront Toronto (#108) and Pan Am Centre.
2. **Terms-of-use posture: dual track.** Build the board-report award capture now (no platform contact), send permission letters now, and enable the portal listing adapter per-body only when a written yes is recorded in the repo — the PMMD/Ariba precedent. Evidence: the platform's public-site TOS prohibits automated extraction only "for commercial purposes" and scopes itself to bidsandtenders.com (the portals are `*.bidsandtenders.ca`); the Vendor ToS is clickwrap ("By clicking the 'I Agree' button") and binds account-holders, but carries a blanket copyright notice ("no one has permission to copy, redistribute, reproduce or republish"); neither portal serves a robots.txt; listings are anonymously visible while bid documents require an account. We treat "settled" as "the body said yes in writing", not "our reading of their terms".
3. **Keyspace: buyer dimension + generic agency tables.** The City spine (`solicitation` / `award` / `noncompetitive` / `bid`) is untouched — every existing consumer keeps its meaning, the #96 composite_award reasoning. Agency records live in `agency_solicitation` / `agency_award` / `agency_bid`, keyed `(buyer_id, native_ref)`, one shape for every platform adapter to come.

## Evidence base (salvaged research corpus, scratchpad 2026-07-18)

- Vendor ToS (via Zendesk/Wayback): clickwrap; §7.5 bars commercial reuse without written approval; §22.1 blanket copyright notice with permission inquiries directed to the vendor.
- Public-site TOS (bidsandtenders.com, GHD-era; platform since acquired by Valsoft): §13 bars "screen scraping" for commercial purposes on the bidsandtenders.com domain; §12 bars excessive usage.
- `torontozoo.bidsandtenders.ca/robots.txt` and `trca.bidsandtenders.ca/robots.txt`: no robots.txt (app shell returned).
- Zendesk help: listings/bid details anonymous; document preview/download requires a free account (clickwrap).
- **TRCA board reports (Laserfiche, TRCA's own hosting, open-data licence)** carry RFP/RFT numbers, the full bidder list, and opening results — e.g. RFT 10039751/10039753 (armour stone, 2023): "four (4) bid submissions were received from the following Proponent(s)" with names. TRCA policy requires open competition at ≥ $100,000, so material awards flow through the board. Note: that 2023 RFT was advertised on **biddingo.com** — TRCA's venue history is mixed; the board record is venue-independent, which is another reason to prefer it.
- **Zoo Board of Management sits on TMMIS as the ZB committee** (e.g. ZB1.06, 2019) — the same agenda/legdocs infrastructure `bid_award_panel.py` already handles. Award reports name winners; 2025-era reports move financials into confidential attachments, so values are partial by design.

## Architecture

Dual track, three components plus a store layer:

- **A. Board-report award capture** (`tb enrich-agencies`) — buildable now, plain-HTTP-mostly, no platform contact.
- **B. Permission letters** — drafts committed to `docs/letters/`; Alex sends them; nothing is sent by tooling.
- **C. Portal listing adapter** (`sources/bids_tenders.py`) — written and tested against fixtures, config-gated OFF per body until a written permission is recorded.

### Store schema (`store/db.py`, `models.py`)

New tables, archive semantics (`first_seen`/`last_seen`, rows never deleted):

- `buyer`: `id INTEGER PK`, `slug TEXT UNIQUE`, `name`, `kind` (`agency` | `corporation`), `partnered INTEGER`, `funding_share REAL NULL`, `platform`, `notes`. Seeded from a hardcoded list in code (the `default_sources()` pattern), initially: `toronto-zoo` (agency, partnered=0, bids&tenders), `trca` (agency, partnered=1, funding_share 0.626, bids&tenders — note mixed Biddingo history).
- `agency_solicitation`: `buyer_id`, `native_ref`, `title`, `status`, `posted_date`, `closing_date`, `portal_url`, `source`. UNIQUE `(buyer_id, native_ref)`. `native_ref` is the body's own identifier, normalized only by trim/uppercase/whitespace-collapse (TRCA `10039751`; Zoo `RFT-42`, `RFP 18 (2018-03)`). A report naming several refs (RFT 10039751, 10039753) yields one row per ref. No join to `document_number` / `workspace_number` / `call_number` is attempted — this is a fourth keyspace by design.
- `agency_award`: `buyer_id`, `native_ref`, `supplier_name_raw`, `supplier_id` (backfilled by linking), `award_amount` (TEXT verbatim), `award_amount_numeric` (REAL via `amount.py`), `value_confidential INTEGER` (1 when the report routes financials to a confidential attachment — distinct from "not published"), `award_date`, `report_url`, `source`. Uniqueness via an `award_line_key`-style expression index COALESCE-ing the nullable parts; `_CONFLICT_TARGETS` gets the matching entry.
- `agency_bid`: `buyer_id`, `native_ref`, `bidder_name_raw`, `bid_price` (TEXT verbatim), `bid_price_numeric`, `outcome`, `report_url`, `source`. UNIQUE `(buyer_id, native_ref, bidder_name_raw)`.
- Amounts follow the three-tier discipline; the labelled tier is deliberately absent until a real need appears. Never aggregate the raw TEXT columns.
- `linking/supplier.py:build_supplier_dimension` extends over `agency_award.supplier_name_raw` and `agency_bid.bidder_name_raw` (via `_NAME_COLUMN`), so suspended firms and cross-buyer supplier behaviour become queryable. This is the point of capturing agencies at all.

### A. `tb enrich-agencies` (new CLI command; not part of `tb sync`)

Per-body isolation à la `pipeline.run_source`: one body failing never stops the other; failures print `FAILED <body>: ...` to stderr and exit non-zero. `--only zoo|trca` filters.

- **TRCA** (no browser): walk `laserfiche.trca.ca` WebLink browse folders for Board of Directors / Executive Committee meetings, fetch agenda-item PDFs over plain HTTP into `<DATA_DIR>/agencies/trca/`, store bytes + sha256 (download queue keyed on `sha256 IS NULL`, the #96 lesson — never on text). `pdftotext`, then a pure parser over the text: item header (`RE:` line, `RFP No.` / `RFT No.` refs — match the ref shape, not the label vocabulary, per the call-number lesson), `RECOMMENDATION` winner, `RATIONALE` bidder list ("bid submissions were received from the following Proponent(s)") and opening results. Emits `agency_solicitation` + `agency_award` + `agency_bid` rows.
- **Zoo** (headed browser for discovery only, behind the `council` extra): discover ZB-series meetings on TMMIS with the `bid_award_panel.py` machinery (probe-and-confirm against each page's stated date — references are not derivable), cache agenda HTML under the existing agendas cache, fetch report PDFs from legdocs over plain HTTP. Pure parser over `pdftotext` text: winner from `RECOMMENDATION`/`SUMMARY`, values where public; when the report declares a confidential attachment, emit the award with `value_confidential=1` and NULL amounts.
- Re-parsing never re-fetches: cached bytes are authoritative, parsers are re-runnable offline (`--reparse`).

### C. `sources/bids_tenders.py` (gated)

Config in `config.py`: `BIDS_TENDERS_PORTALS = [{slug, portal_url, enabled}]`, `enabled=False` for both bodies, with a comment stating the flip condition: a written permission recorded in `docs/permissions/` and referenced in the commit that flips it. When enabled for a body it fetches **anonymous listing metadata only** — native ref, title, status, dates — rate-limited, and upserts `agency_solicitation` with `overwrite=True`: the portal is authoritative for listing fields. The board-report pass writes `agency_solicitation` with `overwrite=False` (it fills what a report happens to name; a portal value always outranks it, and COALESCE keeps NULL from wiping either way). **Bid documents are never fetched** under this design regardless of permission state (they sit behind the clickwrap); expanding to documents would be a new design with the body's explicit document-level consent. The concrete fetch mechanics (the SPA's JSON layer vs rendered HTML) are determined at enablement time, within whatever the permission specifies; the parser is written against recorded fixtures either way. Not appended to `default_sources()` until at least one body is enabled.

### B. Permission letters (`docs/letters/`)

Two drafts, committed: `2026-07-18-toronto-zoo-portal-permission.md` (addressee: Zoo procurement/purchasing office; exact name and address confirmed from torontozoo.com/business at send time) and `2026-07-18-trca-portal-permission.md` (procurement@trca.ca). The ask: read-only, rate-limited periodic fetch of publicly visible listing metadata from the body's bids&tenders portal, for a public non-commercial civic archive; offering attribution, a published contact, and immediate cessation on request. TRCA's letter notes the Bill 97 timeline as the reason for urgency. Letters are sent by Alex, not by tooling; a yes is recorded verbatim in `docs/permissions/` before any fetch is enabled.

### Export (`export/document.py`)

A `buyers` array in the artifact: per buyer `{slug, name, kind, partnered, funding_share, platform, solicitations, awards, bids}` with deterministic ORDER BY. **Headline aggregates remain City-only**; agency data is additive, in its own section, so no existing consumer's counts change meaning. Partnered buyers carry their flag and funding share so consumers can segment.

## Failure handling and drift

- Per-body isolation as above; partial rows commit.
- Parsers raise loudly on structural drift (the suspended-firms precedent) rather than silently yielding fewer rows; the Laserfiche walker and ZB discovery log counts so a silent zero is visible.
- No `schema_check.py` coverage — these are not OData/CKAN feeds; drift surfaces through the parsers' own strictness, as with Ariba.

## Testing

Offline, fixture-based, no network — house rule. Fixtures from the salvaged corpus: the TRCA armour-stone report (two refs, four bidders, results), the TRCA VOR/appraisal report (Vendor-of-Record shape), Zoo ZB1.06 (2019, public winner), Zoo red-panda/perimeter-fence reports (2025, confidential attachments). Parser tests cover: multi-ref items, bidder-list extraction, confidential flagging, ref-shape matching against label variation, and the unequal/ambiguous cases refusing rather than guessing (the #94 rule). Portal-adapter parsing tested against recorded listing fixtures; the fetch path stays untested until enablement (nothing to record without permission). Zoo browser-discovery tests skip without the `council` extra, like council tests today.

## Out of scope / deferred

- Bid documents from the portal — behind clickwrap; would need a new design and document-level consent.
- Historical portal listings (closed opportunities) — whether they are anonymously visible is unknown until enablement; the board-report record covers awards regardless.
- Other bids&tenders bodies (Ontario municipalities) — out of the archive's scope entirely.
- OCR for image-only PDFs, if any appear in the Laserfiche corpus — same ceiling as #96.

## Recording

Once this spec is approved: comment the three gate decisions on #135 (and cross-reference from #103), so the "decide once" questions are answered where the issues said they would be. #109 and #132 get a pointer when implementation lands.
