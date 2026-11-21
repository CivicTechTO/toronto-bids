# City of Toronto council, agenda, and award sources

Besides the core OData and CKAN feeds, the scraper enriches City of Toronto solicitations from council agendas, Bid Committee composite reports, Award Summary Forms, and committee staff reports.

For crosswalks, scoped bid tables, composite participation, scanned PDFs, and bulk historical backfills, see:

- [city-call-crosswalk.md](city-call-crosswalk.md)
- [city-document-aliases.md](city-document-aliases.md)
- [scoped-city-bids.md](scoped-city-bids.md)
- [city-native-participation.md](city-native-participation.md)
- [image-pdf-text.md](image-pdf-text.md)
- [bd-report-recovery.md](bd-report-recovery.md)
- [city-bulk-backfill.md](city-bulk-backfill.md)

## Council agendas (`tb enrich-titles`)

Implemented in `sources/bid_award_panel.py` and `sources/legacy_titles.py` (opt-in enrichment, not `Source` classes).

Two agenda series share the same structure:

- **BA**: Bid Award Panel (2017-2025).
- **BD**: Bid Committee (2009-2016).

The Bid Award Panel was abolished on 2025-10-01 by By-law 766-2025, which moved award authority up to $30M to the Chief Procurement Officer. The cached agenda corpus is complete; awards above $30M go to a Standing Committee or City Council.

### Scraping and term rollover

- TMMIS blocks headless browsers behind Akamai, so scraping uses one headed Chromium instance for the run. Raw HTML is cached under `<DATA_DIR>/council/agendas/`, and re-parsing runs offline.
- Meeting references cannot be predicted from schedules because older terms omit meeting numbers and dates can be out of order. `discover_meetings` probes URLs and verifies each page's stated date.
- Active council terms (such as EP and ZB) roll over automatically from dated evidence (#225): the prober extends the latest four-year term seed and checks the next term's first agenda. Only a confirmed successor meeting whose date has passed closes the previous term; prepublished future agendas do not close a term early. `.closed_terms.json` records each closed term's last meeting and successor date. Missing or invalid pages never confirm a term, and cached wrong-term pages are refetched. Historical BA/BD series keep their configured closure list (#177).

### Titles

The City publishes placeholder document numbers instead of titles for most solicitations (#70). Enrichment passes fill `title` only where it is currently `NULL`; a City-published title always wins.

- **Provenance lives in `title_source`, never `source`.** OData owns `source` and overwrites it on every sync. `title_source` is omitted from the `Solicitation` model so `db.upsert_row` cannot clobber it, and is set only for real titles.
- **Precedence**: Archived Ariba posting `<title>` tags outrank Bid Award Panel headings (handled in `legacy_titles`).
- **Matching**: Ariba-era items match on printed 10-digit document numbers. Pre-Ariba items use a guarded supplier-and-value fallback (see [bd-report-recovery.md](bd-report-recovery.md)).

### Agenda bid tables

Agendas list every bidder, including losing bids, in HTML `<table>` markup (#84). `parse_bid_tables` supports two layouts (#94):

- **BA (row-major)**: one table row per bidder.
- **BD (column-grouped)**: full columns packed inside single cells. `_cell_lines` reads the markup line by line (plain `text_content()` fuses `<p>` tags) and zips the bidder and price columns positionally. Mismatched column lengths are refused so one stray line cannot shift later rows.

The BD parser runs only when the row-major parser declines, scoped to direct child rows. Multiline HST headings and referenced trailing notes are stripped before checking name/price alignment. Successful re-derivation replaces stale pairs for that item; unreadable items keep prior rows and report a review failure.

2009-2012 agendas usually contain composite reports with bid counts rather than full bidder lists, though specific exceptions exist and require source review.

Two caveats:

- `hst_basis` matters: never mix prices across tax bases.
- `bid_price` is not `award_amount` (bids exclude contingency). Non-numeric outcomes written in the price column (such as `Non-Compliant` or `No bid`) stay in the raw string column with `bid_price_numeric = NULL`.

### Staff reports (`background_pdf`, kind `bgrd`)

Agendas link directly to staff-report PDFs, attributed to the agenda item that links them. `--reports` downloads them over plain HTTP.

- The download queue checks `sha256 IS NULL`, never `text IS NULL`, so image-only PDFs are not re-downloaded on every run (#96).
- As measured in #83, panel staff reports do not add missing titles (untitled solicitations were staff-delegated and have no panel report) and their bid lists are not cleanly readable with rule-based parsers.

## Pre-feed composite awards (`composite_award`, #96)

The City's OData and CKAN feeds have very few awards for 2009-2011. For those years, Bid Committee composite-report appendices serve as the award record, stored in `composite_award` keyed by Call Number.

- **Extraction**: Uses the `composite` LLM corpus. `call_number` is `normalize_call_number(reference)` with the raw string kept in `call_number_raw`. References without a valid Call Number shape are refused and logged (#216).
- **Amount**: Use the first net-of-taxes figure (initial term, excluding option years), which matched the feed's `award_amount` on 98.6% of overlapping appendices. Option-year and "total potential" figures can be twice as large.
- **Amount storage**: Store the bare amount, not the surrounding phrase. `amount.py:parse_amount` is strict and leaves phrases like `"$420,000.00 net of all applicable taxes"` with a NULL numeric value.
- **Split awards**: Stored as one row per winner. The LLM returns one award entry per winner with its own amount (note that unlike the old #98 parser check, a model output that fuses two firms into one name would not be caught automatically).

## Award Summary Forms (`sources/award_summary.py`, #114)

`tb enrich-awards --download` archives the Toronto Bids Portal's Award Summary Form PDF for each award, and bids are extracted through the `award_summary` corpus.

- Uses plain HTTP against the same OData spine (`feis_solicitation_published`). PDFs are linked via `uploadedFilesStaff[].bin_id` at `c3api_upload/retrieve/pmmd_solicitations/{bin_id}`. `secure.toronto.ca` is not Akamai-gated.
- The portal's "available for 18 months" rule is a client-side filter (`Latest_Date_Awarded gt <today-18mo>`). Omitting it returns awarded records back to 2010, though the City could enforce it server-side later.
- `bid.reference` is nullable and `bid_key` coalesces both identifiers. Panel bids carry a council reference (and, pre-2019, no document number); award-summary and committee bids carry a document number.
- Coverage is bounded: Award Summary Forms exist only for awards over $500,000 (the abolished Bid Award Panel had no dollar floor), so bid coverage for smaller awards drops off after October 2025.

## Committee and Council awards (`sources/committee_awards.py`, #164)

`tb enrich-committee-awards` (offline by default; `--scrape` drives Chromium) follows voting-record CSVs to agenda items and staff-report PDFs, then extracts bids via the `committee` corpus. It only checks items that name a spine solicitation and do not already have bids. Report files are stored by content hash under `documents/committee_award/<sha256>.pdf`.

Coverage from this path is small because discovery matches only agenda titles containing a 10-digit document number. Most committee items are amendments or non-competitive contracts, and most competitive awards never go to committee. When a committee report covers multiple contracts, each contract's bids key on its own document number if printed.

## Suspended-firm council enrichment (`sources/council.py`)

`tb enrich-council` runs monthly on the 1st inside `tb nightly`. For each `suspended_firm.council_authority`, it fetches the City Council decision from TMMIS (headed Chromium) and extracts linked staff-report PDFs with `pdftotext`.
